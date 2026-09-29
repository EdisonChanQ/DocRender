import os
import sys
import threading
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)

import servicemanager  # noqa: E402
import uvicorn  # noqa: E402
import win32event  # noqa: E402
import win32service  # noqa: E402
import win32serviceutil  # noqa: E402

from app.config import settings  # noqa: E402

SERVICE_NAME = "FileTextParser"
SERVICE_DISPLAY_NAME = "File Text Parser Service"
SERVICE_DESCRIPTION = "FastAPI service that extracts text from PDFs, images and documents."


class FileTextParserService(win32serviceutil.ServiceFramework):
    _svc_name_ = SERVICE_NAME
    _svc_display_name_ = SERVICE_DISPLAY_NAME
    _svc_description_ = SERVICE_DESCRIPTION

    def __init__(self, args):
        super().__init__(args)
        self._stop_event = win32event.CreateEvent(None, 0, 0, None)
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        if self._server is not None:
            self._server.should_exit = True
        win32event.SetEvent(self._stop_event)

    def SvcDoRun(self):
        servicemanager.LogInfoMsg(f"{SERVICE_NAME} starting on {settings.host}:{settings.port}")
        config = uvicorn.Config(
            "app.main:app",
            host=settings.host,
            port=settings.port,
            log_level="info",
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, name="uvicorn", daemon=True)
        self._thread.start()
        win32event.WaitForSingleObject(self._stop_event, win32event.INFINITE)
        servicemanager.LogInfoMsg(f"{SERVICE_NAME} stopped")


if __name__ == "__main__":
    win32serviceutil.HandleCommandLine(FileTextParserService)
