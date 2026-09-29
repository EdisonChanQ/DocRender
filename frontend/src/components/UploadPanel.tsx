import { useRef } from "react";

interface Props {
  file: File | null;
  onSelect: (file: File | null) => void;
  disabled?: boolean;
}

/** 与后端 /file-job/register 的 ALLOWED_EXTS 对齐（仅 PDF/图片，不含文本类） */
const ACCEPT = ".pdf,.png,.jpg,.jpeg,.bmp,.tif,.tiff";

/**
 * 纯文件选择器：只负责「选文件 / 清空」，注册入队动作由父级表单统一负责。
 * （原同步解析逻辑已随首页改为「注册入队」而移除）
 */
export default function UploadPanel({ file, onSelect, disabled = false }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        hidden
        disabled={disabled}
        onChange={(event) => {
          onSelect(event.target.files?.[0] ?? null);
          event.target.value = "";
        }}
      />
      <div
        className="dropzone"
        role="button"
        aria-disabled={disabled}
        style={disabled ? { opacity: 0.55, cursor: "not-allowed" } : undefined}
        onClick={() => {
          if (!disabled) inputRef.current?.click();
        }}
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault();
          if (disabled) return;
          onSelect(event.dataTransfer.files?.[0] ?? null);
        }}
      >
        <p>{file ? file.name : "点击选择文件，或拖拽到此处"}</p>
        <span>支持：PDF / PNG / JPG / BMP / TIFF</span>
      </div>
      {file && !disabled && (
        <button type="button" style={{ marginTop: 8 }} onClick={() => onSelect(null)}>
          清空已选
        </button>
      )}
    </>
  );
}
