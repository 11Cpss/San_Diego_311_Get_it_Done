# Lesson 9: CI/CD with GitHub Actions

## What you'll build and why it matters

You write **`.github/workflows/ci.yml`**, the workflow that checks every push and pull request: lint, all tests, the Lambda image build, Terraform formatting and validation, and the dbt build.

> **Until you finish this lesson, CI checks nothing.** The stub has one placeholder job that prints a message, so every push shows a green tick that proves nothing. The real checks start the moment your `ci.yml` lands.

`deploy.yml` (main → production) is given. You read it, because it uses the same building blocks plus cloud credentials.

Why this piece? "How do you stop broken code reaching production?" is asked in every data-engineering interview. Writing CI yourself means you can say what each job proves, why it needs no credentials, and what it can't catch.

## Concept

- **CI (continuous integration):** every push and pull request is checked automatically. A red ✗ means "don't merge".
- **CD (continuous deployment):** when code lands on `main`, a robot deploys it the same way every time: build the image, push it to ECR, `terraform apply`, update the Lambdas, `databricks bundle deploy`.
- **A workflow** is a YAML file in `.github/workflows/`. `on:` says when it runs. `jobs:` run **in parallel** on fresh virtual machines (`runs-on: ubuntu-latest`) unless one `needs:` another. Each job has `steps`: either `uses:` (a reusable action, such as `actions/checkout@v4`) or `run:` (shell commands). `defaults.run.working-directory` sets where `run:` steps execute.
- **Least privilege for CI too.** `permissions: contents: read` gives the job's `GITHUB_TOKEN` read access only. CI never needs secrets: pull requests from forks don't get them anyway, and a check that needs production credentials is a deploy, not a check.
- **No stored cloud keys for CD.** `deploy.yml` asks GitHub for an OIDC token (`permissions: id-token: write`) and trades it for one-hour AWS credentials. The AWS role trusts only this repo's `prod` environment (lesson 5), which can require a manual approval.
- **Pin your actions.** `uses: actions/checkout@v4` runs whatever that tag points to. `@main` runs whatever someone pushed most recently, which is a supply-chain risk.

A YAML gotcha you'll meet: in YAML 1.1 (PyYAML) the bare key `on` parses as the boolean `true`. GitHub reads it correctly; Python scripts that read workflows must look for `True`. The checker does.

## How it fits the pipeline

```
push / PR ──► ci.yml [YOU]  (4 parallel jobs, no credentials)
                python     ruff + pytest (tests/, ml/tests, databricks/tests)
                docker     docker build -f docker/Dockerfile .
                terraform  fmt -check, init -backend=false, validate   (infra/)
                dbt        dbt parse + build --target duckdb            (dbt/)
merge to main ──► deploy.yml [given]  environment: prod (approval) ─► OIDC ─► ECR ─► terraform apply
                                    ─► Lambdas updated ─► databricks bundle deploy
```

What each job catches that nothing else does:

- **python:** logic bugs, including the lesson checkers once each lesson is done.
- **docker:** a dependency that won't install in the Lambda base image.
- **terraform:** HCL that `python-hcl2` parses but Terraform rejects (a wrong argument name, a type error), and formatting. Nobody has ever run `terraform validate` on this code, so this job is its first real check.
- **dbt:** SQL that doesn't compile, and data tests that fail on the committed fixtures.

If CI is wrong (say the terraform job runs in the repo root), it either fails on every PR or passes without checking anything. The second case is worse, because nobody notices.

Unfinished lesson stubs don't make CI red. The root `conftest.py` reports a test that hits a `NotImplementedError("[lesson N] ...")`, or a file still marked `LESSON N STUB`, as **skipped**. CI stays green while you work through the lessons, and `scripts/lessons.py check N` uses `--lesson-strict` to turn those skips into failures.

## Code walkthrough

Open these two files:

1. **`.github/workflows/deploy.yml`**: the same building blocks you'll use, plus credentials:
   ```yaml
   permissions:
     id-token: write            # required for OIDC
   concurrency:
     group: deploy-prod
     cancel-in-progress: false  # never kill a half-finished terraform apply
   jobs:
     deploy:
       environment: prod        # approval gate + the only identity AWS trusts
   ```
   The image is tagged with `github.sha`, so every deploy traces to a commit, and a rollback means deploying the old tag. `terraform init` gets its backend from `-backend-config` flags. CI will use `-backend=false` instead, which needs no AWS access.
2. **`conftest.py`** (repo root): the short hook (about 60 lines) that turns unfinished-lesson failures into skips. Read it so you know what a green CI does and doesn't promise.

## Your task

```bash
python scripts/lessons.py check 9      # fails until you're done
```

Replace the stub with a workflow named `ci` that has:

1. **Triggers:** `pull_request` and `push` (all branches).
2. **Permissions:** `contents: read` only; no `secrets.` anywhere.
3. **A Python job:** checkout; `actions/setup-python@v5` with `python-version: "3.12"` (the Lambda base image's version, so quote it); `actions/setup-java@v4` with `distribution: temurin` and `java-version: "17"` (pyspark's parser tests). Install with `pip install -e ".[dev,ml]"`, `pip install -r databricks/requirements-dev.txt` and `pip install python-hcl2 dbt-duckdb`. Then `ruff check .`, then `pytest -v` with `env: MLFLOW_ALLOW_FILE_STORE: "true"`.
4. **A Docker job:** checkout, then `docker build -f docker/Dockerfile -t sd311-lambda:ci .` (the repo root is the build context). No push.
5. **A Terraform job:** `defaults.run.working-directory: infra`; `hashicorp/setup-terraform@v3` with `terraform_version: "1.9.8"`; then `terraform fmt -check -recursive`, `terraform init -backend=false` and `terraform validate`.
6. **A dbt job:** working directory `dbt`; setup-python 3.12; `pip install dbt-duckdb`; `env: DBT_PROFILES_DIR: .`; then `dbt parse --target duckdb` and `dbt build --target duckdb`.

Every `uses:` must be pinned to a version tag. Job names are up to you: the checker finds jobs by what they run.

**Done when** `python scripts/lessons.py check 9` prints `PASS` (9 tests). If you're stuck, use `python scripts/lessons.py diff 9`, or `solve 9 --yes`, which backs your version up first.

## Own it

1. Why can the terraform job validate without AWS credentials? What exactly does `-backend=false` skip?
2. Why does CI have no secrets and read-only permissions, while `deploy.yml` has `id-token: write`? What could a malicious pull request do if CI had the deploy role?
3. Unfinished lessons show up as *skipped* in CI. Why is that useful right now, and what's the risk? What one flag would you add to the pytest step once all nine lessons are done?
4. Why is `cancel-in-progress: false` important for deploys but not for CI?

**Change one thing and predict.** In `src/sd311/ingest.py`, change `raw_key` to use `snapshot=` instead of `snapshot_date=`. Before running anything, predict which test files fail and which CI job would go red. Would the docker, terraform or dbt jobs notice? Then run `python -m pytest tests -q` and compare. Revert.

## When you have a GitHub repo (optional, later)

1. Push the repo to GitHub (private is fine) and open the **Actions** tab. Your `ci` workflow runs on the push.
2. Make the `raw_key` change above on a branch (`git switch -c break-ci`), push it, and open a pull request. Watch the `python` job go red and find the failing assertion in its log.
3. In Settings → Branches, add a rule on `main` requiring the `python`, `docker`, `terraform` and `dbt` checks. The merge button is now blocked.
4. Fix, push, watch it go green, then merge or close.
5. Before the first deploy, create the `prod` environment with required reviewers and the variables listed at the top of `deploy.yml`.

## Tradeoffs

| Choice | Upside | Downside |
|---|---|---|
| Deploy on every push to main | small, frequent, boring deploys | needs good tests and an approval gate |
| `apply -auto-approve` in CD | no human typing in the loop | review the *plan* in the PR instead (future: a plan-on-PR job) |
| Tag images by git SHA | traceable, immutable-ish | ECR fills up, so a lifecycle policy keeps the last 10 |
| `databricks/setup-cli@main` in deploy.yml | always the latest CLI | less reproducible; pin a version for stability |
| Parallel CI jobs | faster feedback | more runner minutes (free for public repos) |
| Stubs skip instead of fail | green CI while lessons are in progress | a skip can hide a missing piece; add `--lesson-strict` when done |
| DuckDB for dbt in CI | no warehouse, no credentials, seconds | doesn't prove the SQL runs on Databricks |

## Interview questions

1. CI vs CD?
2. How does your pipeline authenticate to AWS from GitHub Actions?
3. How do you roll back?
4. Why validate Terraform in CI without credentials?
5. What stops a bad deploy?
6. Why pin GitHub Actions to a version?
7. What does your CI *not* catch?

## Time

| part | working | minutes |
|---|---|---|
| Read | about 1,500 words ÷ 150 wpm = 10, plus 2 files × 5 = 10 | 20 |
| Build | ci.yml, four jobs (medium, 30) | 30 |
| Check and explain | 1 piece × 10 + 10 for Own it | 20 |
| **Total** | 20 + 30 + 20 = 70 | **70** |

## Answers

1. **CI vs CD:** CI verifies every change automatically; CD ships verified changes to an environment automatically.
2. **Auth:** GitHub OIDC. The job gets a signed token, and STS `AssumeRoleWithWebIdentity` exchanges it for temporary credentials on a role whose trust policy only allows this repo's `prod` environment. No access keys are stored anywhere.
3. **Rollback:** re-run the deploy for the previous commit (image tag = SHA), or `aws lambda update-function-code` to the old tag.
4. **Terraform without credentials:** `init -backend=false` downloads providers without touching state, and `validate` checks syntax, types and references. That catches most mistakes on every PR, cheaply and safely.
5. **Bad deploys:** required CI checks on PRs, the `prod` environment approval, `cancel-in-progress: false` so an apply is never cut in half, and alarms after deploy (Lambda errors, JobMissing).
6. **Pinning:** a tag or SHA runs known code. A branch like `@main` runs whatever was pushed last, a supply-chain risk and a reproducibility problem.
7. **Blind spots:** anything that needs real cloud resources. Terraform `plan`/`apply` against real state, IAM denials at runtime, Databricks-only SQL (COPY INTO, Delta), the city's server blocking us, and data drift. Those are covered by the day-one check, alarms and the monitor Lambda.
