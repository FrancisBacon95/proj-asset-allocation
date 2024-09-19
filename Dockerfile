FROM python:3.9

WORKDIR /app

# Copy the necessary files to the working directory
COPY . .

# Set PYTHONPATH
ENV PYTHONPATH "${PYTHONPATH}:/app"

# Debug: List files in /app and print PYTHONPATH

RUN ls -R /app

RUN echo $PYTHONPATH

# Install dependencies
RUN pip install poetry
RUN poetry export --without-hashes --format=requirements.txt > requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
 