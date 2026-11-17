# P7 sensor dashboard app image. Chromium + its system libraries come from the pinned Playwright base image,
# matching playwright==1.65.1 in pyproject.toml.
FROM mcr.microsoft.com/playwright/python:v1.65.1-noble

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Riyadh MPLCONFIGDIR=/tmp/matplotlib
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --break-system-packages . \
 && mkdir -p /app/reports && chown pwuser:pwuser /app/reports

COPY docker/entrypoint.sh /usr/local/bin/p7-entrypoint
RUN chmod 755 /usr/local/bin/p7-entrypoint

USER pwuser
EXPOSE 8790
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8790/healthz', timeout=4).status == 200 else 1)"
ENTRYPOINT ["p7-entrypoint"]
CMD ["serve"]
