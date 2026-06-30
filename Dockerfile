FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV HOME=/home/user
ENV PATH=/home/user/.local/bin:$PATH
ENV HALALSTREAM_STORAGE_DIR=/home/user/app/storage

RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates ffmpeg curl nodejs \
  && update-ca-certificates \
  && rm -rf /var/lib/apt/lists/* \
  && useradd -m -u 1000 user

USER user
WORKDIR /home/user/app

COPY --chown=user requirements.txt .
RUN python -m pip install --user --no-cache-dir --upgrade pip \
  && python -m pip install --user --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch torchaudio \
  && python -m pip install --user --no-cache-dir -r requirements.txt

COPY --chown=user . .
RUN mkdir -p /home/user/app/storage/jobs

EXPOSE 8000

CMD ["sh", "start.sh"]
