from pydantic import BaseModel, Field


class QrHit(BaseModel):
    text: str = Field(description="解码文本")
    symbol: str = Field(default="QRCODE")
    rect: dict | None = Field(default=None, description="二维码在图中的位置（像素）")


class QrDecodeFileResult(BaseModel):
    filename: str
    ok: bool = Field(description="该文件是否解码流程成功（识别到 0 个也算成功，error 为空）")
    qr_count: int = Field(default=0, description="识别到的二维码数量")
    texts: list[str] = Field(default_factory=list)
    server_ms: float = Field(default=0.0, description="后端处理耗时（毫秒），并发压测指标")
    error: str | None = None


class OcrLine(BaseModel):
    text: str
    score: float = Field(description="识别置信度 0~1")
    box: list[list[int]] | None = Field(default=None, description="文字四角坐标（像素）")


class OcrFileResult(BaseModel):
    filename: str
    ok: bool
    line_count: int = Field(default=0)
    lines: list[OcrLine] = Field(default_factory=list)
    full_text: str = Field(default="", description="按行拼接的全文")
    tier: str = Field(default="small", description="使用的模型档位")
    channel: str | None = Field(default=None, description="实际使用的识别通道：detect/crop")
    server_ms: float = Field(default=0.0, description="后端处理耗时（毫秒），并发压测指标")
    error: str | None = None
