"""模板配准：把模板字段坐标映射到上传页图的坐标系。

## 核心原则：只映射坐标，不动图

模板字段坐标（template_field.x/y/w/h）存在**范本图空间**（= ref_image 的实际像素系，
如 1253×1764），不是模板声明的 width/height（如 2504×3525）。

上传页图与范本图之间总存在「微小旋转 / 缩放 / 平移」差异（实测一张看起来完全正常的页，
整体偏移约 9px）。要在这张页上按坐标裁字段，必须把这个差异算出来。

**正确做法**：求一个「范本坐标 → 页图坐标」的仿射矩阵 W，只把字段四角过一遍 W，
在**原分辨率页图**上裁切。图片本身一个像素都不改。

**错误做法（已踩坑，勿回退）**：把页图 warp 到范本尺寸再在低分图上裁 —— 分辨率腰斩
（3510→1764），像素细节丢失。用户明确指出过这一点。

## 为什么不用 matchTemplate 定位

整页模板场景下（模板声明尺寸 ≈ 页图尺寸），`cv2.matchTemplate` 的滑窗没有位移空间，
得分退化为「整体相似度」：实测页0 得 0.3129 命中、页1 得 0.2992 失败，只差 0.0008，
阈值全靠运气；而且"命中"后反而把页裁掉了 16px。故整页模板一律走 ECC。

`find_template_boxes`（matchTemplate）仅保留给**局部块模板**做粗定位，
即「一页 A4 上排 N 张支票，模板只描述单张支票块」的场景。

## 受控畸变实验结论（probe14 / probe15，决定采用本方案）

   场景         ECC     纯缩放IoU   ECC映射IoU
   无畸变      1.0000     1.000       1.000
   轻度        0.9970     0.608       1.000
   中度        0.9973     0.307       1.000
   重度        0.8035     0.176       0.949

真实数据复核（probe15，用字段 ref_image 样本图在页上 NCC 匹配当真值）：
   IoU 均值 纯缩放 0.851 → ECC 0.928。

## ECC 相关度可直接当"匹配度"用

实测双峰分布：同版式 0.83~0.996、异版式 0.05~0.23，断崖在 ~0.5。
比 `TM_CCOEFF_NORMED`（同版式 0.29~0.35 糊在一起）可解释得多。

## 两种落地方式（二选一，勿混用）

**A. 只映射坐标（warp 都不做）**：字段四角过 W 再 × s，在**原页图**上裁切。
   页图产物保持原样（未纠正）。见 map_box。

**B. 重渲染到声明规格（当前采用）**：用 rectify_matrix 得 M=(s/k)·W，
   `warpAffine(page, M, (tw,th), WARP_INVERSE_MAP)` 得到**端正**的新图，
   尺寸严格等于模板声明 width/height。此后字段坐标退化为纯等比缩放 f×k
   （见 rectified_field_box），不再需要逐字段走矩阵。

方式 B 的产物是端正的（旋转/缩放/平移已被渲染吸收），下游（人工审计、
前端预览、整页文本提取）都对得上；且输出 ≈ 原分辨率（s≈k 时缩放≈1），
**不是"缩到范本尺寸再放大"那种腰斩方案**。

probe27 受控畸变实验（故意取 s≠k）钉死矩阵形式：
    畸变真值 旋转 1.2° / 平移 (14,-11)
    B 反向 (s/k)·W    → 残余旋转 -0.0001°  ✅（与 E 数学等价）
    E 正向 (k/s)·W⁻¹  → 残余旋转 -0.0001°  ✅
    A 正向 (s/k)·W    → 残余旋转 -2.6421°  ❌
    C 正向 (s/k)·W⁻¹  → 残余旋转 +0.7041°  ❌
（注：`warpAffine(M, WARP_INVERSE_MAP)` ≡ `warpAffine(inv(M))`，B 与 E 恒等。）
"""

from __future__ import annotations

import cv2
import numpy as np

# —— matchTemplate（仅块模板粗定位用）阈值 ——
MIN_SCORE = 0.30
RELATIVE_SCORE = 0.50
# 近失候选：低于 th 但 ≥ 此下限的峰交给 ECC 复核救援。
# matchTemplate 对底色/纹理差异敏感（白底范本 vs 绿底划线付支票实测仅 0.2982），
# ECC 对此稳健（同一张 0.7073）——单闸门会静默丢真票（JOB202609300828497D99 p0 3张只出2张）。
NEAR_MISS_FLOOR = 0.15
NEAR_MISS_MAX = 5  # 每页最多救援尝试次数（防噪声峰拖慢）

# —— ECC 相关度分档（决定页的处置策略）——
SCORE_AUTO = 0.75     # ≥ 自动通过
SCORE_REJECT = 0.45   # < 拒绝（整页回退，仅文本提取）

# —— 模板类型 ——
PAGE_MODE_FULL = "full_page"   # 整页模板：声明尺寸 ≈ 页图尺寸，不切块
PAGE_MODE_BLOCK = "block"      # 局部块模板：一页多块，需粗定位

# 判为整页模板的尺寸偏差容忍度（声明尺寸 vs 页图尺寸，宽高都要满足）。
# 0.10 = 门框归一化容差：用户可按标准门框设定 width/height（如 1600×2300），
# 上传件按 dpi 渲染后与门框有小幅差异（如 200dpi 整页 1655×2340，差 ~3.4%）仍判
# full_page，由 ECC 配准 scale 自适应缩放。dpi 是取样密度非物理真值，不以它锁死门框。
# 真正的 block 模板声明尺寸远小于页图（差 >10%），不受影响。
FULL_PAGE_TOLERANCE = 0.10

ECC_MAX_ITER = 300
ECC_EPS = 1e-7
ECC_GAUSSIAN_BLUR = 5

# —— 粗对齐（ECC 初值）——
# ECC 自单位矩阵起步的捕获范围只有 ~±15px；实测 JOB20260930041810CFC7 p2
# 真实偏移 21px，ECC 原地收敛返回未对齐相关度 0.32 → 误拒（实际可达 0.89）。
# 策略：identity 起步保持原口径不变；仅当结果未达 auto 档（或有理由怀疑没走到
# 对齐位置）时，才用 phaseCorrelate 平移初值重跑一次，取更优解。
# 实测：p0/p1/p3 分数与旧实现逐位一致，p2 0.3222 → 0.8845（auto）。
PHASE_MIN_RESPONSE = 0.03   # 响应度低于此（低纹理/纯色页）视为不可信，不重试
PHASE_MAX_SHIFT = 0.45      # 平移超过图幅 45% 视为不可信（正常页不会偏这么多）


def to_gray(img) -> np.ndarray:
    """PIL Image → 灰度 numpy 数组（uint8）。"""
    return np.array(img.convert("L"))


def score_band(score: float | None) -> str:
    """相关度分档标签：auto / low / reject / unknown。供结果提示图例使用。"""
    if score is None:
        return "unknown"
    if score >= SCORE_AUTO:
        return "auto"
    if score >= SCORE_REJECT:
        return "low"
    return "reject"


def detect_page_mode(
    ref_size: tuple[int, int],
    page_size: tuple[int, int],
    declared_size: tuple[int | None, int | None] | None = None,
) -> str:
    """按声明尺寸自动判别模板类型（无声明时退回宽高比判据）。

    ref_size / page_size 为 (width, height)。
    整页模板：模板声明 width/height 与实际页图尺寸偏差 < 2%（宽高都满足）。
    """
    pw, ph = page_size
    if declared_size:
        dw, dh = declared_size
        if dw and dh and pw > 0 and ph > 0:
            if abs(dw - pw) / dw < FULL_PAGE_TOLERANCE and abs(dh - ph) / dh < FULL_PAGE_TOLERANCE:
                return PAGE_MODE_FULL
            return PAGE_MODE_BLOCK
    # 无声明尺寸：退化为宽高比比对（范本比 ≈ 页图比 → 整页）
    rw, rh = ref_size
    if rw <= 0 or rh <= 0 or pw <= 0 or ph <= 0:
        return PAGE_MODE_BLOCK
    ref_ratio = rw / rh
    page_ratio = pw / ph
    if min(ref_ratio, page_ratio) / max(ref_ratio, page_ratio) > 0.97:
        return PAGE_MODE_FULL
    return PAGE_MODE_BLOCK


def _coarse_shift(ref_gray: np.ndarray, page_gray: np.ndarray) -> tuple[float, float] | None:
    """相位相关求「页相对范本」的全局平移初值（ECC 捕获范围 ~±15px 不够用的补救）。

    cv2.phaseCorrelate(ref, page) 返回 (dx, dy)：page 内容相对 ref 的位移，
    即范本坐标 → 页缩图坐标的平移分量（probe 实测钉死符号：p2 dx,dy=(-3.1,-21.9)，
    W0 平移列直接取该值 → ECC 收敛 cc=0.8845；取反或单位阵 → 0.20/0.33）。
    响应度过低（低纹理/纯色）或位移超图幅 45% 视为不可信，返回 None 走单位阵。
    """
    h, w = ref_gray.shape
    try:
        win = cv2.createHanningWindow((w, h), cv2.CV_32F)
        (dx, dy), resp = cv2.phaseCorrelate(
            ref_gray.astype(np.float32), page_gray.astype(np.float32), win
        )
    except cv2.error:
        return None
    if resp < PHASE_MIN_RESPONSE:
        return None
    if abs(dx) > w * PHASE_MAX_SHIFT or abs(dy) > h * PHASE_MAX_SHIFT:
        return None
    return float(dx), float(dy)


def _identity_ncc(a: np.ndarray, b: np.ndarray) -> float:
    """两图（同尺寸 float 通道）零平移处的归一化互相关 —— ECC cc 的未对齐基准值。"""
    am = a.ravel() - a.mean()
    bm = b.ravel() - b.mean()
    denom = float(np.linalg.norm(am) * np.linalg.norm(bm))
    return float(am @ bm / denom) if denom > 0 else 0.0


def register_ecc(ref_gray: np.ndarray, page_at_ref_size_gray: np.ndarray) -> tuple[float, np.ndarray | None]:
    """ECC 仿射配准。

    输入两张**同尺寸**灰度图：ref 是范本图，page 是被缩放到范本尺寸的页图。
    OpenCV 约定 template=ref、input=page，返回的 W 满足：
        page_coord ≈ W · ref_coord        （齐次坐标，2×3 仿射）
    即 **W 的方向是「范本坐标 → 页（缩到范本尺寸的）坐标」**。

    probe15 实测确认：范本(0,0)→页(0.2,-8.9)、(1253,1764)→(1252.8,1772.9)，
    方向正确，物理含义是「这张页整体比范本下移约 9px」。

    两种用法（对应模块文档 A / B）：
      A. 投影坐标：字段四角 `W @ corner * s`（s=页宽/范本宽），在原页图上裁。
      B. 重渲染：`rectify_matrix()` 把它换算成 M=(s/k)·W，配 WARP_INVERSE_MAP
         把页图重渲染成模板声明规格（产物端正，尺寸==声明 width/height）。
    ⚠ 别把 W 直接喂给 warpAffine：`W` 是「输出→输入」方向（因为它是范本→页），
      但还差一个 s/k 的尺度换算，且必须配 WARP_INVERSE_MAP（probe27 已钉死）。

    返回 (相关系数 0~1, W)；配准失败返回 (0.0, None)。
    """
    if ref_gray.shape != page_at_ref_size_gray.shape:
        return 0.0, None
    if ref_gray.shape[0] < 8 or ref_gray.shape[1] < 8:
        return 0.0, None
    ref_f = ref_gray.astype(np.float32) / 255.0
    page_f = page_at_ref_size_gray.astype(np.float32) / 255.0
    crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, ECC_MAX_ITER, ECC_EPS)

    # ① identity 起步（保持原口径，绝大多数正常页一次即达 auto）
    best_cc = 0.0
    best_W: np.ndarray | None = None
    try:
        cc, W = cv2.findTransformECC(ref_f, page_f, np.eye(2, 3, dtype=np.float32),
                                     cv2.MOTION_AFFINE, crit, None, ECC_GAUSSIAN_BLUR)
        best_cc, best_W = float(cc), W
    except cv2.error:
        pass  # 全黑/不收敛 → 让 ② 再试一次，仍失败则 (0.0, None)

    # ② 未达 auto 才用相位相关平移初值重跑（捕获范围 ~±15px → 全图）。
    #    取两次更优解，绝不比 identity 更差；identity 已 auto 的页零额外开销。
    if best_cc < SCORE_AUTO:
        shift = _coarse_shift(ref_gray, page_at_ref_size_gray)
        if shift is not None:
            W0 = np.array([[1.0, 0.0, shift[0]], [0.0, 1.0, shift[1]]], dtype=np.float32)
            try:
                cc2, W2 = cv2.findTransformECC(ref_f, page_f, W0,
                                               cv2.MOTION_AFFINE, crit, None, ECC_GAUSSIAN_BLUR)
                if float(cc2) > best_cc:
                    best_cc, best_W = float(cc2), W2
            except cv2.error:
                pass

    # ③ 护栏：两次都没超过 identity 处基准相关度 → 视为配准失败
    #    （identity 起步且没挪动时，cc 就等于未对齐相关度，不该当匹配度用）
    if best_W is not None and best_cc > 0:
        id_ncc = _identity_ncc(ref_f, page_f)
        if best_cc <= id_ncc + 1e-6 and np.allclose(best_W[:, 2], 0, atol=0.5):
            return 0.0, None
    return best_cc, best_W


def map_box(
    W: np.ndarray,
    scale: float,
    x: float,
    y: float,
    w: float,
    h: float,
    offset: tuple[float, float] = (0.0, 0.0),
) -> tuple[int, int, int, int]:
    """把范本空间的一个框映射到页图空间（取四角映射后的外接矩形）。

    W      : 范本坐标 → 页（缩到范本尺寸）坐标
    scale  : 页图宽 / 范本宽（把"页缩图坐标"放大回原分辨率）
    offset : 块内局部坐标 → 页图绝对坐标的平移（块模板用；整页模板传 (0,0)）
    """
    corners = np.array(
        [[x, y], [x + w, y], [x, y + h], [x + w, y + h]], dtype=np.float64
    )
    homo = np.hstack([corners, np.ones((4, 1))])
    mapped = (W @ homo.T).T * scale
    mapped[:, 0] += offset[0]
    mapped[:, 1] += offset[1]
    xs, ys = mapped[:, 0], mapped[:, 1]
    x0, y0 = float(xs.min()), float(ys.min())
    return (
        int(round(x0)),
        int(round(y0)),
        int(round(float(xs.max()) - x0)),
        int(round(float(ys.max()) - y0)),
    )


def _page_gray_at_ref_size(page_gray: np.ndarray, ref_size: tuple[int, int]) -> np.ndarray:
    rw, rh = ref_size
    ph, pw = page_gray.shape
    if (pw, ph) == (rw, rh):
        return page_gray
    interp = cv2.INTER_AREA if (pw > rw or ph > rh) else cv2.INTER_LINEAR
    return cv2.resize(page_gray, (rw, rh), interpolation=interp)


def align_page(
    page_gray: np.ndarray,
    ref_gray: np.ndarray,
    *,
    declared_size: tuple[int | None, int | None] | None = None,
) -> dict:
    """整页 ECC 配准：求「范本坐标 → 页图(缩到范本尺寸)坐标」的整体变换。

    返回 dict：
      score      : ECC 相关度 0~1（配准失败为 0.0）
      band       : auto / low / reject
      matrix     : 2×3 仿射（范本坐标 → 页缩图坐标），失败为 None
      scale      : 页图宽 / 范本宽
      page_mode  : full_page / block（按声明尺寸判别）
      ref_size   : (w, h) 范本图尺寸
      page_size  : (w, h) 页图尺寸
      declared_size: (w, h) 模板声明规格（未声明为 None）
    """
    ref_h, ref_w = ref_gray.shape
    page_h, page_w = page_gray.shape
    scale = page_w / ref_w if ref_w else 1.0
    mode = detect_page_mode((ref_w, ref_h), (page_w, page_h), declared_size)
    small = _page_gray_at_ref_size(page_gray, (ref_w, ref_h))
    score, W = register_ecc(ref_gray, small)
    dw, dh = (declared_size or (None, None))
    return {
        "score": score,
        "band": score_band(score) if W is not None else "reject",
        "matrix": W,
        "scale": scale,
        "page_mode": mode,
        "ref_size": (ref_w, ref_h),
        "page_size": (page_w, page_h),
        "declared_size": (dw, dh),
    }


def rectify_matrix(
    W: np.ndarray | None,
    page_size: tuple[int, int],
    declared_size: tuple[int, int],
    ref_size: tuple[int, int],
) -> tuple[np.ndarray | None, tuple[int, int]]:
    """构造「把页图重渲染成模板声明规格」的仿射矩阵。

    设 R=范本坐标、P=页原图坐标、D=声明规格坐标，W 为 ECC 求出的
    「范本坐标 → 页(缩到范本尺寸)坐标」矩阵，s=页宽/范本宽，k=声明宽/范本宽：
        P = s · W · R,   D = k · R  →  R = D/k  →  P = (s/k)·W·D
    warpAffine 的 M 是「输出坐标 → 输入坐标」，故：
        M = (s/k)·W ，输出尺寸 (tw, th)
    ⚠ W 为 None（无范本/配准失败）时返回 (None, 原页尺寸)，调用方走降级路径。

    返回 (M, (out_w, out_h))；M 为 None 表示无法重渲染。
    """
    tw, th = declared_size
    pw, ph = page_size
    rw, rh = ref_size
    if W is None or rw <= 0 or rh <= 0 or tw <= 0 or th <= 0:
        return None, (pw, ph)
    s = pw / rw
    k = tw / rw
    M = ((s / k) * np.asarray(W, dtype=np.float64)).astype(np.float32)
    return M, (int(tw), int(th))


def rectify_image(page_img, M: np.ndarray | None, out_size: tuple[int, int]):
    """按 M 把页图重渲染为声明规格；M 为 None 时原样返回。

    只做一次仿射重采样（输出 ≈ 原分辨率，非「缩到范本尺寸再放大」，不丢细节）。
    """
    if M is None:
        return page_img
    import cv2

    from PIL import Image

    arr = np.array(page_img.convert("RGB"))
    out = cv2.warpAffine(
        arr, M, (int(out_size[0]), int(out_size[1])),
        flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE,
    )
    return Image.fromarray(out)


def rectified_field_box(field, k: float) -> tuple[int, int, int, int]:
    """重渲染到声明规格后，字段坐标退化为纯等比缩放 f×k（无需矩阵）。

    k = 声明规格宽 / 范本宽。这比逐字段走矩阵更简单，也不会引入外接矩形外扩。
    """
    return (
        int(round(field.x * k)),
        int(round(field.y * k)),
        int(round(field.width * k)),
        int(round(field.height * k)),
    )


def align_block(
    page_gray: np.ndarray,
    ref_gray: np.ndarray,
    box: tuple[int, int, int, int],
) -> dict:
    """对一个已粗定位的块框做 ECC 局部精配准（局部块模板用）。

    box 为页图空间的 (x, y, w, h)。返回结构与 align_page 对齐，
    但 matrix/scale 是**块内局部坐标**系，须配合 offset=(box.x, box.y) 使用。
    """
    bx, by, bw, bh = box
    roi = page_gray[by : by + bh, bx : bx + bw]
    if roi.size == 0:
        return {"score": 0.0, "band": "reject", "matrix": None, "scale": 1.0, "offset": (float(bx), float(by))}
    ref_h, ref_w = ref_gray.shape
    small = _page_gray_at_ref_size(roi, (ref_w, ref_h))
    score, W = register_ecc(ref_gray, small)
    return {
        "score": score,
        "band": score_band(score) if W is not None else "reject",
        "matrix": W,
        "scale": (bw / ref_w) if ref_w else 1.0,
        "offset": (float(bx), float(by)),
    }


def find_template_boxes(
    page_gray: np.ndarray,
    ref_gray: np.ndarray,
    *,
    min_score: float = MIN_SCORE,
    relative: float = RELATIVE_SCORE,
    near_miss_floor: float = NEAR_MISS_FLOOR,
) -> tuple[list[tuple[int, int, int, int]], list[tuple[float, tuple[int, int, int, int]]], float]:
    """在页图中粗定位模板实例（**仅供局部块模板使用**）。

    返回 (boxes, near_miss, threshold)：
      boxes     : 过阈值的块，页图坐标 [(x, y, w, h), ...]，按 y 升序
      near_miss : 未过阈值但 ≥ near_miss_floor 的独立峰 [(coarse_score, (x,y,w,h)), ...]
                  按分数降序 —— matchTemplate 对底色/防伪纹理差异敏感，这类
                  "近失"里可能有真票（如绿底划线付支票 0.2982 < th=0.3876），
                  调用方应对其做 ECC 复核救援，而不是静默丢弃
      threshold : 本次实际使用的阈值（供日志/落库说明"为什么丢"）
    无有效命中返回 ([], [], th 或 0)。

    ⚠ 整页模板场景**不要**调用本函数：模板尺寸 ≈ 页图尺寸时滑窗无位移空间，
    得分退化为整体相似度，阈值不可靠（实测 0.3129 命中 / 0.2992 失败）。
    整页模板请直接用 align_page。
    """
    ph, pw = page_gray.shape
    rh, rw = ref_gray.shape
    if rh < 8 or rw < 8 or ph < 8 or pw < 8:
        return [], [], 0.0
    if pw < rw or ph < rh:
        # 范本比页图大：无法匹配
        return [], [], 0.0

    k = rw / pw  # 页图缩放系数（缩到「页宽 = 范本宽」）
    if k >= 1.0:
        sp, back = page_gray, 1.0
    else:
        sh = max(rh, int(round(ph * k)))
        sp = cv2.resize(page_gray, (rw, sh), interpolation=cv2.INTER_AREA)
        back = 1.0 / k
        if sp.shape[0] < rh or sp.shape[1] < rw:
            return [], [], 0.0

    res = cv2.matchTemplate(sp, ref_gray, cv2.TM_CCOEFF_NORMED)
    row_max = res.max(axis=1)
    best = float(row_max.max())
    th = max(min_score, best * relative)

    # 收集所有 ≥ near_miss_floor 的连续行组：≥ th 的是正式块，[floor, th) 的是近失候选
    ys = np.where(row_max >= near_miss_floor)[0]
    if ys.size == 0:
        return [], [], th

    # 连续 y 归为一组（间隔 < 范本高一半视为同一块，避免一块出多个峰）
    groups: list[list[int]] = [[int(ys[0])]]
    for y in ys[1:]:
        if int(y) - groups[-1][-1] < rh / 2:
            groups[-1].append(int(y))
        else:
            groups.append([int(y)])

    bw, bh = int(round(rw * back)), int(round(rh * back))
    boxes: list[tuple[int, int, int, int]] = []
    near: list[tuple[float, tuple[int, int, int, int]]] = []
    for g in groups:
        y = max(g, key=lambda i: row_max[i])
        x = int(np.argmax(res[y]))
        bx = max(0, min(int(round(x * back)), pw - 1))
        by = max(0, min(int(round(y * back)), ph - 1))
        box = (bx, by, min(bw, pw - bx), min(bh, ph - by))
        if float(row_max[y]) >= th:
            boxes.append(box)
        else:
            near.append((float(row_max[y]), box))
    boxes.sort(key=lambda b: b[1])
    near.sort(key=lambda t: -t[0])  # 分数高的优先救援
    return boxes, near[:NEAR_MISS_MAX], th
