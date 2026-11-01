FROM mambaorg/micromamba:2.3.3
COPY --chown=$MAMBA_USER:$MAMBA_USER science/environment.yml /tmp/environment.yml
RUN micromamba install -y -n base -f /tmp/environment.yml && micromamba clean --all --yes
ARG MAMBA_DOCKERFILE_ACTIVATE=1
WORKDIR /app
COPY --chown=$MAMBA_USER:$MAMBA_USER science /app/science
RUN python -m pip install -r /app/science/requirements-py311.lock && python -m pip install --no-deps /app/science
ENV BOLTZFIX_DATA=/data BOLTZFIX_ALLOW_LIVE=0 BOLTZFIX_SPONSOR_CONFIRMED=0
EXPOSE 8000
CMD ["python", "-m", "boltzfix.cli", "serve", "--host", "127.0.0.1"]
