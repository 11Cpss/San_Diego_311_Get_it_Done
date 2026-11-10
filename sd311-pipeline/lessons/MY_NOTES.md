# My notes on the SD311 lessons

These are my explanations in my own words. I'll add to them as I work through the lessons and correct anything I misunderstood.

## Lesson 4: schema.py and ingest.py

### What I understand about the schema

`schemas.py` basically tells us what structure the city's CSV needs to have: which columns are required, which ones are optional, and which values have to pass extra checks. The AWS Lambda downloads the CSV and calls this validation before loading the data further. If the file fails, the ingest code puts it in S3 quarantine and stops that run. It doesn't just silently process bad data.

This reminds me of Pydantic validating the body of a POST request in FastAPI. Here, Pandera validates a pandas table from a CSV instead of an API request body. The schema is intentionally flexible about extra columns and blanks in some required columns, because those cases can be valid.

### What I understand about ingest.py

`ingest.py` runs the steps around the file: download the city's CSV, validate it, save the original CSV and a manifest in S3, and, when the Databricks part is enabled, convert the valid data to Parquet and send it to Databricks for processing.

`upload_file()` is the step that **sends the Parquet file bytes** to a folder in a Databricks Volume. It uses an HTTP `PUT` request. This is the function that actually moves the file to Databricks.

`run_now()` is a separate step that **starts a Databricks job** after the files have been uploaded. It uses an HTTP `POST` request. Its `body` is a Python dictionary that the `requests` library sends as JSON. That JSON contains a `job_id`, which tells Databricks which job to start. It does **not** contain the CSV or Parquet file, and it does not turn the schema into JSON.

`raise_for_status()` makes the code report an HTTP error if Databricks rejects the request. On success, Databricks replies with a `run_id`, which identifies this particular job run. Getting a `run_id` means Databricks accepted the request to start the job; it does **not** yet prove the job finished successfully or that it sent a file. We would check the run's status separately.

My current mental picture:

```text
City CSV → Lambda downloads → schema checks → original CSV saved in S3
                                            → valid data converted to Parquet
                                            → upload_file() moves Parquet to Databricks
                                            → run_now() starts the Databricks job
                                            → the job processes the uploaded data
```

For the first AWS-only deployment, the Databricks steps are turned off, so the valid files land in S3 without being uploaded to Databricks.

### Where the files actually travel

The daily pipeline runs in AWS, so the files don't need to go through or be saved on my computer. The Lambda GETs the CSV from the city's website and holds its bytes in memory. It validates the CSV and saves the original bytes to S3. If the file is bad, it saves those bytes under S3 `quarantine/` instead.

When Databricks is enabled, the **same Lambda** also converts the valid in-memory table to Parquet bytes and calls `upload_file()` to send those bytes directly to a Databricks Volume. In this code, Databricks does not pull that Parquet file out of S3. After all files upload, `run_now()` sends a small JSON instruction telling Databricks to start the job; it does not move a file.

My short version: city → Lambda memory → S3 copy; Lambda memory → Databricks Parquet upload → start the Databricks job. My laptop is only where I write and test the code.

### How I understand the two Databricks functions now

The Lambda downloads the city's CSV and uses the Pandera schema to validate its table. If it's valid, the ingest code saves the original CSV in S3. Then, if Databricks is enabled, `to_parquet_bytes()` turns that same in-memory table into Parquet bytes. `upload_file()` sends those bytes from the Lambda to a Databricks cloud folder. The upload does not read the CSV back from S3 and does not do the conversion itself.

After all the uploads succeed, `run_now()` tells Databricks which processing job to start. It receives a **job ID** that was already configured for that Databricks job; the job ID does not come from `upload_file()`. Databricks responds with a **run ID** for this particular start attempt. The run ID tells us which run to check later, but by itself it does not prove the processing finished successfully.

My short explanation: S3 saves a copy of the original CSV so we can go back to it if something goes wrong. `upload_file()` runs inside the Lambda and sends the Parquet bytes straight to Databricks. Once all the files are there, `run_now()` sends the already-known job ID to Databricks. Databricks returns a run ID for that particular run. That means the start request was accepted; I would check the run status to confirm it actually finished.

## Lesson 5: AWS permissions and schedule

My understanding so far: this lesson sets up what the ingest AWS Lambda is allowed to do. That includes saving and reading specific files in S3, reading its stored settings in SSM, publishing CloudWatch metrics, and writing logs. It also sets a schedule so AWS invokes the Lambda every day at 6:00 AM Pacific time. Writing and checking this lesson locally does not create anything in my AWS account; deployment comes later.

### My interview explanation of the daily flow

At 6:00 AM Pacific, EventBridge Scheduler starts the ingest Lambda directly. The Lambda sends a GET request to the city's website for the CSV, then runs the data through my Pandera schema. If it passes, the Lambda saves the original CSV and a manifest in S3. If Databricks is enabled, the same Lambda also makes a Parquet version, uploads it to Databricks, and starts the Databricks job. For the first AWS-only deployment, that Databricks step is off. If the CSV fails validation, the Lambda saves it in S3 quarantine, publishes a data-quality metric, and stops the run. GitHub Actions is for future deployments of the infrastructure; it does not fetch the daily city file.

I save the original CSV so that if something goes wrong later, I can go back and verify exactly what the city sent. The Pandera schema decides whether the file is usable. If the CSV cannot be parsed, or a required column or value fails the schema, the ingest code quarantines it and sends `DataQualityFailures = 1`; the CloudWatch alarm is set to trigger when the sum is greater than zero.

### How I understand Docker, ECR, and Lambda

ECR is where I push and store my Docker image in AWS. The image packages the Python runtime, the libraries my code needs, and my `sd311` code, so Lambda has something it can run. I build the image on my computer, push it to ECR, and then Lambda uses that image.

The image is not every setting for the Lambda. Terraform separately sets things like the Lambda's memory, timeout, environment variables, and IAM role. In particular, the `SD311_BUCKET` environment variable tells the running Lambda which S3 bucket to use. My short version: the image is the code and its Python environment; Terraform connects that image to an AWS Lambda and gives it its AWS settings and permissions.
