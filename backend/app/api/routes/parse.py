import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from app.config import settings
from app.core.errors import ParserError, UnsupportedFileTypeError
from app.schemas.parse import ParseResult, SupportedTypes
from app.services.parse_service import parse_service

router = APIRouter()


@router.get("/supported", response_model=SupportedTypes)
def supported_types() -> SupportedTypes:
    return parse_service.supported_types()


@router.post("/upload", response_model=ParseResult)
async def parse_upload(file: UploadFile = File(...)) -> ParseResult:
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing filename")

    settings.ensure_dirs()
    suffix = Path(file.filename).suffix.lower()
    temp_path = settings.upload_dir_resolved / f"{uuid.uuid4().hex}{suffix}"

    try:
        size = 0
        with temp_path.open("wb") as buffer:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                buffer.write(chunk)

        return parse_service.parse(temp_path, file.filename)
    except UnsupportedFileTypeError as exc:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)) from exc
    except ParserError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    finally:
        await file.close()
        if not settings.keep_uploaded_files:
            temp_path.unlink(missing_ok=True)
