FROM python:3.9-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy only runtime code. Local databases, credentials and Git history must
# never become part of an image, even when the build context contains them.
COPY ai/ ai/
COPY api/ api/
COPY db/ db/
COPY engine/ engine/
COPY models/ models/
COPY schemas/ schemas/
COPY static/ static/
COPY config.py main.py LICENSE ./

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
