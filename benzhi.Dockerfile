# 烟气处理控制平台镜像：离线构建，不访问公网。
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app:/app/vendor \
    PIP_NO_INDEX=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# vendored 直接依赖：PyYAML 纯 Python 源码，不需要联网安装。
COPY vendor ./vendor
COPY fgd ./fgd
COPY config ./config
COPY README.md ./README.md

RUN python -m compileall -q fgd && mkdir -p var/state

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/readiness', timeout=3).status == 200 else 1)"

CMD ["python", "-m", "fgd", "--config", "config/fgd.yaml", "serve", "--host", "0.0.0.0", "--port", "8080"]
