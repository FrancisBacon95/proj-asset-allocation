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

# Configure Poetry to not use virtualenvs (ensures global installation)
RUN poetry config virtualenvs.create false

# Install dependencies with Poetry
RUN poetry install --no-root