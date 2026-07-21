import logging

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import settings

logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
)


@app.exception_handler(IntegrityError)
async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    """Backstop for constraints the services do not check up front.

    A rejected constraint is a client mistake, not a server fault, so it must not surface as
    a 500. Services that can say *what* conflicts should still check first and raise their
    own 409 — this only guarantees the generic case is never a 500. The driver message is
    logged rather than returned: it names tables and constraints.
    """
    logger.warning('IntegrityError on %s %s: %s', request.method, request.url.path, exc.orig)
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={'detail': 'The request conflicts with existing data'},
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=['*'],
    allow_headers=['*'],
)

app.include_router(api_router, prefix=settings.api_v1_prefix)
app.mount('/images', StaticFiles(directory=settings.images_dir, check_dir=False), name='images')
