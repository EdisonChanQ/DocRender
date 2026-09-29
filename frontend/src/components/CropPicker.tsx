import { useCallback, useEffect, useRef, useState } from "react";

export interface CropRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface OverlayRect extends CropRect {
  label: string;
  color?: string;
  /** false = 仅展示，不拦截鼠标（避免相邻框干扰当前编辑的拖拽调整） */
  interactive?: boolean;
}

interface CropPickerProps {
  /** 底图 data_url（原图像素坐标系） */
  imageUrl: string;
  imgWidth: number;
  imgHeight: number;
  /** 受控的框选矩形（原图像素坐标） */
  crop: CropRect | null;
  onCropChange: (crop: CropRect | null) => void;
  /** 适配宽度时的显示容器高度上限（px） */
  maxHeight?: number;
  /** 叠加显示的已有框（如已标注字段），坐标为底图像素 */
  overlays?: OverlayRect[];
  onOverlayClick?: (index: number) => void;
  hint?: string;
  /** 显示横纵参考网格线（每 50 原图像素一格，中线加粗） */
  grid?: boolean;
  /** 变化时才重置缩放（默认取 imageUrl）。旋转微调会换 imageUrl，
   *  用稳定 resetKey 避免每次调角度都丢失缩放。 */
  resetKey?: string;
}

const ZOOM_MIN = 0.5;
const ZOOM_MAX = 6;
const MIN_SIZE = 4;

type Dir = "nw" | "n" | "ne" | "e" | "se" | "s" | "sw" | "w";
const HANDLES: Dir[] = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];

/**
 * 图片框选组件：
 * - 画布空白处拖拽 = 画新框；框本体拖拽 = 整块移动；8 向手柄 = 调宽高；
 * - Ctrl + 滚轮缩放（原生 wheel 监听，阻止浏览器整页缩放）；
 * - 框选坐标始终以原图像素为准，缩放只影响显示。
 */
export function CropPicker({
  imageUrl,
  imgWidth,
  imgHeight,
  crop,
  onCropChange,
  maxHeight = 640,
  overlays,
  onOverlayClick,
  hint = "按住拖拽框选（Ctrl+滚轮缩放）",
  grid = false,
  resetKey,
}: CropPickerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLDivElement>(null);
  const [fitScale, setFitScale] = useState(1);
  const [zoom, setZoom] = useState(1);
  const [drag, setDrag] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  const [resize, setResize] = useState<{ dir: Dir; orig: CropRect } | null>(null);
  const [move, setMove] = useState<{ orig: CropRect; dx: number; dy: number } | null>(null);

  const scale = fitScale * zoom; // 显示像素 / 原图像素

  // 适配尺寸计算：只更新 fitScale，不重置 zoom——
  // 保存后布局微变（出滚动条等）会触发 observer，若重置则用户每次都要重新放大。
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const compute = () => {
      const availW = el.clientWidth - 4;
      const s = Math.min(availW / imgWidth, maxHeight / imgHeight, 1);
      setFitScale(s > 0 ? s : 1);
    };
    compute();
    const ro = new ResizeObserver(compute);
    ro.observe(el);
    return () => ro.disconnect();
  }, [imgWidth, imgHeight, maxHeight]);

  // 换图时才重置缩放（旋转微调不换 resetKey，保持缩放）
  useEffect(() => {
    setZoom(1);
  }, [resetKey ?? imageUrl]);

  // 原生 wheel 监听（非 passive）：React onWheel 无法 preventDefault，
  // Ctrl+滚轮会被浏览器吃掉变成整页缩放。
  // 监听挂在整个组件容器上：指针在留白/工具栏也能缩放图片，不漏给浏览器。
  // 只认 Ctrl——不认 Alt：Windows 上 Ctrl+Alt 是 Intel 核显热键前缀，
  // 驱动截获后会造成系统级 modifier 卡键（Ctrl 像没释放、点击变异常多选）。
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const onWheelNative = (e: WheelEvent) => {
      if (!e.ctrlKey) return;
      e.preventDefault();
      const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
      setZoom((z) => Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z * factor)));
    };
    el.addEventListener("wheel", onWheelNative, { passive: false });
    return () => el.removeEventListener("wheel", onWheelNative);
  }, []);

  const toImgCoords = useCallback(
    (clientX: number, clientY: number) => {
      const el = canvasRef.current;
      if (!el) return { x: 0, y: 0 };
      const rect = el.getBoundingClientRect();
      const x = Math.min(Math.max((clientX - rect.left) / scale, 0), imgWidth);
      const y = Math.min(Math.max((clientY - rect.top) / scale, 0), imgHeight);
      return { x, y };
    },
    [scale, imgWidth, imgHeight],
  );

  function onPointerDown(e: React.PointerEvent) {
    if (drag || resize || move) return;
    canvasRef.current?.setPointerCapture(e.pointerId);
    const p = toImgCoords(e.clientX, e.clientY);
    setDrag({ x0: p.x, y0: p.y, x1: p.x, y1: p.y });
  }

  function startResize(e: React.PointerEvent, dir: Dir) {
    if (!crop) return;
    e.stopPropagation();
    canvasRef.current?.setPointerCapture(e.pointerId);
    setResize({ dir, orig: { ...crop } });
  }

  function startMove(e: React.PointerEvent) {
    if (!crop) return;
    e.stopPropagation();
    canvasRef.current?.setPointerCapture(e.pointerId);
    const p = toImgCoords(e.clientX, e.clientY);
    setMove({ orig: { ...crop }, dx: p.x - crop.x, dy: p.y - crop.y });
  }

  function onPointerMove(e: React.PointerEvent) {
    if (resize) {
      const p = toImgCoords(e.clientX, e.clientY);
      onCropChange(applyResize(resize.dir, resize.orig, p, imgWidth, imgHeight));
      return;
    }
    if (move) {
      const p = toImgCoords(e.clientX, e.clientY);
      const nx = Math.min(Math.max(p.x - move.dx, 0), imgWidth - move.orig.w);
      const ny = Math.min(Math.max(p.y - move.dy, 0), imgHeight - move.orig.h);
      onCropChange({ x: Math.round(nx), y: Math.round(ny), w: move.orig.w, h: move.orig.h });
      return;
    }
    if (!drag) return;
    const p = toImgCoords(e.clientX, e.clientY);
    setDrag({ ...drag, x1: p.x, y1: p.y });
  }

  function onPointerUp() {
    if (resize) {
      setResize(null);
      return;
    }
    if (move) {
      setMove(null);
      return;
    }
    if (!drag) return;
    const x = Math.min(drag.x0, drag.x1);
    const y = Math.min(drag.y0, drag.y1);
    const w = Math.abs(drag.x1 - drag.x0);
    const h = Math.abs(drag.y1 - drag.y0);
    setDrag(null);
    if (w < 8 || h < 8) return;
    onCropChange({
      x: Math.round(x),
      y: Math.round(y),
      w: Math.round(Math.min(w, imgWidth - x)),
      h: Math.round(Math.min(h, imgHeight - y)),
    });
  }

  function stepZoom(factor: number) {
    setZoom((z) => Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z * factor)));
  }

  const liveRect: CropRect | null = drag
    ? {
        x: Math.round(Math.min(drag.x0, drag.x1)),
        y: Math.round(Math.min(drag.y0, drag.y1)),
        w: Math.round(Math.abs(drag.x1 - drag.x0)),
        h: Math.round(Math.abs(drag.y1 - drag.y0)),
      }
    : crop;

  const box = liveRect
    ? {
        left: liveRect.x * scale,
        top: liveRect.y * scale,
        width: liveRect.w * scale,
        height: liveRect.h * scale,
      }
    : null;

  // 参考网格：每 50 原图像素一格（随缩放联动），横纵线 + 中线加粗
  const gridStyle: React.CSSProperties | undefined = grid
    ? {
        backgroundImage: `
          repeating-linear-gradient(0deg, rgba(37,99,235,0.18) 0 1px, transparent 1px ${(50 * scale).toFixed(2)}px),
          repeating-linear-gradient(90deg, rgba(37,99,235,0.18) 0 1px, transparent 1px ${(50 * scale).toFixed(2)}px)`,
        backgroundSize: `${(imgWidth * scale).toFixed(2)}px ${(imgHeight * scale).toFixed(2)}px`,
      }
    : undefined;

  return (
    <div className="crop-picker" ref={containerRef}>
      <div className="crop-picker__toolbar">
        <button type="button" onClick={() => stepZoom(1 / 1.25)} title="缩小">−</button>
        <span className="crop-picker__zoom">{Math.round(scale * 100)}%</span>
        <button type="button" onClick={() => stepZoom(1.25)} title="放大">＋</button>
        <button type="button" onClick={() => setZoom(1)} title="恢复适配">适配</button>
        <span className="muted">Ctrl+滚轮缩放；拖框整体移动，手柄调宽高</span>
      </div>
      <div className="crop-picker__scroller" style={{ maxHeight }}>
        <div
          ref={canvasRef}
          className="crop-picker__canvas"
          style={{ width: imgWidth * scale, height: imgHeight * scale }}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
        >
          <img src={imageUrl} alt="模板预览" draggable={false} />
          {grid && (
            <div className="crop-picker__grid" style={gridStyle}>
              <div
                className="crop-picker__grid-center-v"
                style={{ left: (imgWidth / 2) * scale }}
              />
              <div
                className="crop-picker__grid-center-h"
                style={{ top: (imgHeight / 2) * scale }}
              />
            </div>
          )}
          {overlays?.map((o, i) => (
            <div
              key={i}
              className="crop-picker__overlay"
              style={{
                left: o.x * scale,
                top: o.y * scale,
                width: o.w * scale,
                height: o.h * scale,
                borderColor: o.color ?? "#2563eb",
                pointerEvents: o.interactive === false ? "none" : undefined,
              }}
              onPointerDown={(e) => {
                if (o.interactive === false) return;
                e.stopPropagation();
                onOverlayClick?.(i);
              }}
              title={o.label}
            >
              <span className="crop-picker__overlay-label" style={{ background: o.color ?? "#2563eb" }}>
                {o.label}
              </span>
            </div>
          ))}
          {box && (
            <>
              <div
                className={`crop-picker__rect${crop && !drag ? " crop-picker__rect--movable" : ""}`}
                style={{ left: box.left, top: box.top, width: box.width, height: box.height }}
                onPointerDown={crop && !drag ? startMove : undefined}
              />
              {!drag &&
                crop &&
                HANDLES.map((dir) => (
                  <div
                    key={dir}
                    className={`crop-handle crop-handle--${dir}`}
                    style={{
                      left: box.left + (dir.includes("w") ? 0 : dir.includes("e") ? box.width : box.width / 2),
                      top: box.top + (dir.includes("n") ? 0 : dir.includes("s") ? box.height : box.height / 2),
                    }}
                    onPointerDown={(e) => startResize(e, dir)}
                  />
                ))}
            </>
          )}
          {!crop && !drag && <div className="crop-picker__hint">{hint}</div>}
        </div>
      </div>
      {crop && (
        <button type="button" className="crop-picker__clear" onClick={() => onCropChange(null)}>
          清除框选
        </button>
      )}
    </div>
  );
}

function applyResize(
  dir: Dir,
  orig: CropRect,
  p: { x: number; y: number },
  imgW: number,
  imgH: number,
): CropRect {
  let { x, y, w, h } = orig;
  const right = orig.x + orig.w;
  const bottom = orig.y + orig.h;
  if (dir.includes("w")) {
    x = Math.min(Math.max(p.x, 0), right - MIN_SIZE);
    w = right - x;
  }
  if (dir === "e" || dir === "ne" || dir === "se") {
    w = Math.max(MIN_SIZE, Math.min(p.x, imgW) - orig.x);
  }
  if (dir.includes("n")) {
    y = Math.min(Math.max(p.y, 0), bottom - MIN_SIZE);
    h = bottom - y;
  }
  if (dir === "s" || dir === "se" || dir === "sw") {
    h = Math.max(MIN_SIZE, Math.min(p.y, imgH) - orig.y);
  }
  return { x: Math.round(x), y: Math.round(y), w: Math.round(w), h: Math.round(h) };
}

export default CropPicker;
