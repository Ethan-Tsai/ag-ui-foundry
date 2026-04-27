FROM python:3.11-slim

WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt ./backend/
RUN pip install --no-cache-dir -r backend/requirements.txt

# Copy backend code
COPY . ./backend/

# Set PYTHONPATH so python backend/server.py works with imports
ENV PYTHONPATH=/app

# Expose backend port
EXPOSE 8000

# Run the server
CMD ["python", "backend/server.py"]
