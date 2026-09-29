from fastapi import APIRouter

from app.api.routes import category, database, file_job, file_page, health, parse, path_config, template, template_field, tools

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(parse.router, prefix="/parse", tags=["parse"])
api_router.include_router(database.router, prefix="/database", tags=["database"])
api_router.include_router(category.router, prefix="/category", tags=["category"])
api_router.include_router(template.router, prefix="/template", tags=["template"])
api_router.include_router(
    template_field.router, prefix="/template/{template_id}/fields", tags=["template-field"]
)
api_router.include_router(path_config.router, prefix="/path-config", tags=["path-config"])
api_router.include_router(file_job.router, prefix="/file-job", tags=["file-job"])
api_router.include_router(file_page.router, prefix="/file-page", tags=["file-page"])
api_router.include_router(tools.router, prefix="/tools", tags=["tools"])
