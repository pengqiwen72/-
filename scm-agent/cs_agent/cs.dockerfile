FROM langchain/langgraph-api:3.12-wolfi-d18f703



# -- Adding local package . --
ADD . /deps/cs_agent
# -- End of local package . --

# -- Adding non-package dependency src --
ADD ./src /deps/outer-src/src
RUN set -ex && \
    for line in '[project]' \
                'name = "src"' \
                'version = "0.1"' \
                '[tool.setuptools.package-data]' \
                '"*" = ["**/*"]' \
                '[build-system]' \
                'requires = ["setuptools>=61"]' \
                'build-backend = "setuptools.build_meta"'; do \
        echo "$line" >> /deps/outer-src/pyproject.toml; \
    done
# -- End of non-package dependency src --
RUN PYTHONDONTWRITEBYTECODE=1 uv pip install --system --no-cache-dir setuptools
ENV UV_HTTP_TIMEOUT=120
ENV UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
# -- Installing all local dependencies --
RUN for dep in /deps/*; do             echo "Installing $dep";             if [ -d "$dep" ]; then                 echo "Installing $dep";                 (cd "$dep" && PYTHONDONTWRITEBYTECODE=1 uv pip install --system --no-cache-dir -e .);             fi;         done
    # -- End of local dependencies install --
# -- Ensure requirements.txt deps are installed (workaround for missing python-dotenv etc.) --
RUN PYTHONDONTWRITEBYTECODE=1 uv pip install --system --no-cache-dir -r /deps/cs_agent/requirements.txt
#ENV LANGGRAPH_AUTH='{"path": "/deps/outer-src/src/auth/auth.py:auth"}'
ENV LANGGRAPH_HTTP='{"app": "/deps/cs_agent/src/webapp/webapp.py:app"}'
ENV LANGSERVE_GRAPHS='{"cs": "/deps/cs_agent/src/graphs/cs_graph/cs_graph.py:graph"}'



# -- Ensure user deps didn't inadvertently overwrite langgraph-api
RUN mkdir -p /api/langgraph_api /api/langgraph_runtime /api/langgraph_license && touch /api/langgraph_api/__init__.py /api/langgraph_runtime/__init__.py /api/langgraph_license/__init__.py
RUN PYTHONDONTWRITEBYTECODE=1 uv pip install --system --no-cache-dir --no-deps --force-reinstall -e /api
# -- End of ensuring user deps didn't inadvertently overwrite langgraph-api --
# -- Removing build deps from the final image ~<:===~~~ --
RUN PYTHONDONTWRITEBYTECODE=1 uv pip install --system --no-cache-dir fastapi==0.125.0

RUN pip uninstall -y pip setuptools wheel
RUN rm -rf /usr/local/lib/python*/site-packages/pip* /usr/local/lib/python*/site-packages/setuptools* /usr/local/lib/python*/site-packages/wheel* && find /usr/local/bin -name "pip*" -delete || true
RUN rm -rf /usr/lib/python*/site-packages/pip* /usr/lib/python*/site-packages/setuptools* /usr/lib/python*/site-packages/wheel* && find /usr/bin -name "pip*" -delete || true
RUN uv pip uninstall --system pip setuptools wheel && rm /usr/bin/uv /usr/bin/uvx

WORKDIR /deps/cs_agent