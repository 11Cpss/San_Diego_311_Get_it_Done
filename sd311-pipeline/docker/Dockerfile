# One image, two Lambdas. Terraform picks the handler per function with
# image_config.command; the CMD below is just the default (ingest).
#
# Build from the repo root:
#   docker build -f docker/Dockerfile -t sd311-lambda .
# Try it locally (the base image ships the Lambda Runtime Interface Emulator):
#   docker run -p 9000:8080 -e SD311_BUCKET=x -e SD311_DATABRICKS_ENABLED=false sd311-lambda
#   curl -d '{}' http://localhost:9000/2015-03-31/functions/function/invocations
FROM public.ecr.aws/lambda/python:3.12

# Dependencies first: this layer is cached until requirements.txt changes.
COPY docker/requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# Then our code (changes often -> small, fast layer). Copying src/sd311 into
# LAMBDA_TASK_ROOT makes `import sd311` work without installing the package.
COPY src/sd311 ${LAMBDA_TASK_ROOT}/sd311

CMD ["sd311.ingest.handler"]
