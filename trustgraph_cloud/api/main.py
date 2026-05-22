from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from trustgraph_cloud.artifacts.store import LocalArtifactStore
from trustgraph_cloud.config import Settings, get_settings
from trustgraph_cloud.jobs.queue import LocalJobQueue
from trustgraph_cloud.jobs.store import LocalJobStore
from trustgraph_cloud.jobs.worker import Worker
from trustgraph_cloud.logging import configure_logging, logger
from trustgraph_cloud.api.routes import router
from trustgraph_cloud.api.upload_routes import router as upload_router
from trustgraph_cloud.api.auth_routes import router as auth_router
from trustgraph_cloud.api.api_key_routes import router as api_key_router


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    """
    Factory that creates a configured FastAPI application.

    Pass a Settings instance in tests to control workspace paths and config
    without mutating module-level state.
    """
    _settings = settings  # captured by lifespan closure

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        s = _settings if _settings is not None else get_settings()
        configure_logging(s.log_level)

        # -- JWT secret -----------------------------------------------------------
        # When jwt_secret is empty, generate an ephemeral one.  Suitable for
        # local dev with auth_required=true; NOT suitable for production (tokens
        # won't survive restarts).
        if s.auth_required and not s.jwt_secret:
            ephemeral_secret = secrets.token_hex(32)
            # Rebind so all closures (auth deps) see the resolved secret
            object.__setattr__(s, "jwt_secret", ephemeral_secret)
            logger.warning(
                "auth.ephemeral_jwt_secret",
                extra={"warning": "Set TRUSTGRAPH_JWT_SECRET for stable tokens across restarts"},
            )

        # -- Auth stores (Phase 3B) -----------------------------------------------
        if s.auth_store == "dynamodb":
            from trustgraph_cloud.auth.dynamodb_auth import (
                DynamoDBApiKeyStore,
                DynamoDBUserStore,
            )
            user_store = DynamoDBUserStore(
                table_name=s.users_table,
                region=s.dynamodb_region,
                endpoint_url=s.aws_endpoint_url,
            )
            api_key_store = DynamoDBApiKeyStore(
                table_name=s.api_keys_table,
                region=s.dynamodb_region,
                endpoint_url=s.aws_endpoint_url,
            )
            logger.info("api.auth_store", extra={"backend": "dynamodb"})
        else:
            from trustgraph_cloud.auth.stores import LocalApiKeyStore, LocalUserStore
            user_store = LocalUserStore()
            api_key_store = LocalApiKeyStore()
            logger.info("api.auth_store", extra={"backend": "local"})

        # -- Job store ------------------------------------------------------------
        if s.job_store == "dynamodb":
            if not s.dynamodb_table:
                raise RuntimeError(
                    "TRUSTGRAPH_DYNAMODB_TABLE must be set when TRUSTGRAPH_JOB_STORE=dynamodb"
                )
            from trustgraph_cloud.jobs.dynamodb_store import DynamoDBJobStore
            job_store = DynamoDBJobStore(
                table_name=s.dynamodb_table,
                region=s.dynamodb_region,
                endpoint_url=s.aws_endpoint_url,
            )
            logger.info("api.job_store", extra={"backend": "dynamodb", "table": s.dynamodb_table})
        else:
            s.jobs_dir.mkdir(parents=True, exist_ok=True)
            job_store = LocalJobStore(s.jobs_dir)
            logger.info("api.job_store", extra={"backend": "local"})

        # -- Job queue ------------------------------------------------------------
        if s.job_queue == "sqs":
            if not s.sqs_queue_url:
                raise RuntimeError(
                    "TRUSTGRAPH_SQS_QUEUE_URL must be set when TRUSTGRAPH_JOB_QUEUE=sqs"
                )
            from trustgraph_cloud.jobs.sqs_queue import SQSJobQueue
            job_queue = SQSJobQueue(
                queue_url=s.sqs_queue_url,
                region=s.sqs_region,
                visibility_timeout=s.sqs_visibility_timeout_seconds,
                wait_time_seconds=s.sqs_wait_time_seconds,
                endpoint_url=s.aws_endpoint_url,
            )
            logger.info("api.job_queue", extra={"backend": "sqs", "queue_url": s.sqs_queue_url})
        else:
            job_queue = LocalJobQueue()
            logger.info("api.job_queue", extra={"backend": "local"})

        # -- Artifact store -------------------------------------------------------
        if s.artifact_store == "s3":
            if not s.s3_bucket:
                raise RuntimeError(
                    "TRUSTGRAPH_S3_BUCKET must be set when TRUSTGRAPH_ARTIFACT_STORE=s3"
                )
            from trustgraph_cloud.artifacts.s3_store import S3ArtifactStore
            artifact_store = S3ArtifactStore(
                bucket=s.s3_bucket,
                prefix=s.s3_prefix,
                region=s.aws_region,
                presigned_url_ttl=s.s3_presigned_url_ttl_seconds,
                endpoint_url=s.aws_endpoint_url,
            )
            logger.info("api.artifact_store", extra={"backend": "s3", "bucket": s.s3_bucket})
        else:
            artifact_store = LocalArtifactStore(s.jobs_dir)
            logger.info("api.artifact_store", extra={"backend": "local"})

        # -- S3 input store (Phase 3A) --------------------------------------------
        if s.s3_bucket:
            from trustgraph_cloud.artifacts.s3_input_store import S3InputStore
            s3_input_store = S3InputStore(
                bucket=s.s3_bucket,
                prefix=s.input_s3_prefix,
                region=s.aws_region,
                upload_ttl=s.upload_url_ttl_seconds,
                endpoint_url=s.aws_endpoint_url,
            )
            logger.info("api.s3_input_store", extra={
                "bucket": s.s3_bucket,
                "prefix": s.input_s3_prefix,
            })
        else:
            s3_input_store = None

        # -- Rate limiter (Phase 3C) ----------------------------------------------
        from trustgraph_cloud.api.rate_limit import RateLimiter
        rate_limiter = RateLimiter(limit_per_minute=s.auth_rate_limit_per_minute)

        app.state.settings = s
        app.state.user_store = user_store
        app.state.api_key_store = api_key_store
        app.state.job_store = job_store
        app.state.artifact_store = artifact_store
        app.state.job_queue = job_queue
        app.state.s3_input_store = s3_input_store
        app.state.upload_url_ttl_seconds = s.upload_url_ttl_seconds
        app.state.rate_limiter = rate_limiter

        # -- Embedded worker (optional) -------------------------------------------
        if s.embedded_worker:
            worker = Worker(
                queue=job_queue,
                job_store=job_store,
                artifact_store=artifact_store,
                settings=s,
                s3_input_store=s3_input_store,
            )
            worker_task = asyncio.create_task(worker.run())
            logger.info("api.startup", extra={
                "workspace": str(s.base_workspace),
                "embedded_worker": True,
                "auth_required": s.auth_required,
            })
        else:
            worker = None
            worker_task = None
            logger.info("api.startup", extra={
                "workspace": str(s.base_workspace),
                "embedded_worker": False,
                "auth_required": s.auth_required,
            })

        yield

        if worker_task is not None:
            worker_task.cancel()
            await asyncio.gather(worker_task, return_exceptions=True)
        if worker is not None:
            await asyncio.get_event_loop().run_in_executor(None, worker.close)
        logger.info("api.shutdown")

    app = FastAPI(
        title="TrustGraph Cloud API",
        description=(
            "Async audit job API for deterministic Solidity trust-boundary analysis. "
            "Phase 1: local execution. Phase 2: SQS/S3/ECS Fargate. Phase 3: auth + uploads."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS — resolve settings now (middleware must be added before routes).
    # Uses the injected settings object if provided (tests), otherwise the
    # module-level singleton so env vars are respected in production.
    _cors_s = settings if settings is not None else get_settings()
    _cors_origins = [o.strip() for o in _cors_s.cors_origins.split(",") if o.strip()]
    if _cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=_cors_origins,
            allow_credentials=_cors_s.cors_allow_credentials,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
        )

    app.include_router(router)
    app.include_router(upload_router)
    app.include_router(auth_router)
    app.include_router(api_key_router)
    return app


# Module-level app instance for `uvicorn trustgraph_cloud.api.main:app`
app = create_app()
