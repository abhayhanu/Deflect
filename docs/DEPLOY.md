# Deploying the live demo

One container holds everything: the API, the built console, the MCP server, Postgres and
Qdrant. The steps below put it on Google Cloud Run or on a Hugging Face Docker Space. Any host
that can run one container with about 2 GB of memory should work the same way.

`PHASE_5_DEPLOYMENT_EXPLAINED.md` at the repository root walks through all of this for someone
who has never deployed anything. This file is the short version.

## Before anything is public

The demo runs `gemini-3.5-flash-lite` with Jev classifying and checking. That setup was
measured as v9 on 7 October 2026: the full run passes the gate with escalation recall 0.97,
29 of 30, and the lowest of three passes of the escalation subset is 0.97 too. It passes with
no room to spare, and the one missed ticket is the same one in three runs of four,
`docs/FAILURES.md` entry 20. v10 is the change aimed at it and is not measured yet.

Deploy a version only when its full run passes the gate and the lowest of three passes of the
escalation subset does too. One passing run can be luck, and a demo is public. Commit first, so
the results files name the code they measured:

```bash
python -m evals.run --subset escalation --reseed --anchor 2026-10-01T10:00:00+00:00 --provider gemini --classifier jev --checker jev --out results_v10_escalation_1.json
python -m evals.run --subset escalation --reseed --anchor 2026-10-01T10:00:00+00:00 --provider gemini --classifier jev --checker jev --out results_v10_escalation_2.json
python -m evals.run --subset escalation --reseed --anchor 2026-10-01T10:00:00+00:00 --provider gemini --classifier jev --checker jev --out results_v10_escalation_3.json
python -m evals.stability results_v10_escalation_1.json results_v10_escalation_2.json results_v10_escalation_3.json
python -m evals.run --subset full --reseed --anchor 2026-10-01T10:00:00+00:00 --provider gemini --classifier jev --checker jev --out results_v10_gemini_jev.json
python -m evals.gate results_v10_gemini_jev.json
```

The numbers in the README's first line come from the full run's file of the version deployed.

## Where to run it

| | Google Cloud Run | Hugging Face Space, CPU Basic | Render, free web service |
| --- | --- | --- | --- |
| Price | free allowance each month, then per second | needs a PRO account, $9 a month | free |
| Memory | what you choose, 2 GB here | 16 GB | 512 MB |
| CPU | what you choose, 1 vCPU here | 2 vCPU | 0.1 CPU |
| Needs a payment card | yes, for the billing account | yes, for PRO | no |
| When unused | stops after 15 idle minutes, wakes on the next visit | pauses after 48 hours, wakes on the next visit | sleeps after 15 minutes |
| Address | `https://NAME-NUMBER.REGION.run.app` | `https://USER-NAME.hf.space` | `https://NAME.onrender.com` |

Checked on 9 October 2026. Hugging Face changed its rules: a Docker Space now needs a paid plan
to create, where the CPU Basic hardware used to be free for everyone. Render's free memory is too
small for an image that runs Postgres, Qdrant and an embedding model in two processes.

Cloud Run's monthly free allowance, per billing account, with the CPU always on as this demo
needs: 240,000 vCPU seconds and 450,000 GiB seconds. At 1 vCPU and 2 GiB the memory runs out first, at about 62
hours of running time. Each visit after an idle spell keeps the instance up for at most 15 idle minutes
after its last request, so the allowance covers a few hundred visits a month.

Nothing is kept between restarts on either host, and for a demo that is what you want. Every
start is a clean shop with the same ten tickets.

## Try the image on your own machine first

I could not build the image where this was written, because Docker was not running there. The
start script was run for real as a non root user, with Postgres. The image build and the Qdrant
start are the two parts that have not been run. So build it once locally before anything else:

```bash
docker build -t deflect-demo .
docker run --rm -p 7860:7860 -e GOOGLE_API_KEY=your_key -e TYPESAFE_API_KEY=your_key deflect-demo
```

Open http://localhost:7860. The inbox fills with ten tickets in about half a minute. In a
second terminal, `docker stats --no-stream` shows how much memory the container uses once the
ten tickets have loaded. Give Cloud Run at least half again as much. If the build fails on the
Qdrant copy, pin the image tag in the `Dockerfile` to the version your `docker compose` runs.

## Put it on Cloud Run

The source is uploaded and built by Cloud Build with the `Dockerfile`, then run. From the
repository root, with the gcloud CLI installed and `gcloud init` done:

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com
gcloud secrets create gemini-key --data-file=../gemini.txt
gcloud secrets create jev-key --data-file=../jev.txt

PROJECT_NUMBER=$(gcloud projects describe $(gcloud config get-value project) --format="value(projectNumber)")
SA=serviceAccount:$PROJECT_NUMBER-compute@developer.gserviceaccount.com
gcloud projects add-iam-policy-binding $(gcloud config get-value project) --member=$SA --role=roles/run.builder
gcloud secrets add-iam-policy-binding gemini-key --member=$SA --role=roles/secretmanager.secretAccessor
gcloud secrets add-iam-policy-binding jev-key --member=$SA --role=roles/secretmanager.secretAccessor

python deploy/export_space.py ../deflect-deploy
cd ../deflect-deploy
gcloud run deploy deflect --source . --region asia-south1 \
  --allow-unauthenticated --execution-environment gen2 \
  --cpu 1 --memory 2Gi --cpu-boost --no-cpu-throttling \
  --min-instances 0 --max-instances 1 --timeout 300 \
  --set-secrets GOOGLE_API_KEY=gemini-key:latest,TYPESAFE_API_KEY=jev-key:latest
```

`gemini.txt` and `jev.txt` each hold one key and nothing else, not even a line break, and sit
outside the folder that is deployed, since everything in that folder is uploaded. Delete them
once the secrets exist. Why each flag is there:

| Flag | Why |
| --- | --- |
| `--no-cpu-throttling` | The ten tickets load in a background thread after startup. With the default billing the CPU is only given during a request, and the load would crawl |
| `--max-instances 1` | Each instance has its own Postgres. Two instances would be two different shops, and one is also a ceiling on cost |
| `--min-instances 0` | Nothing runs, and nothing is billed, when nobody is looking |
| `--execution-environment gen2` | Full Linux, for Postgres and Qdrant inside the container |
| `--cpu-boost` | Extra CPU during startup, so a cold start is shorter |
| `asia-south1` | Mumbai, and a Tier 1 price region |

Cloud Run sets `PORT` to 8080 and the start script listens on it. The service's address is
printed at the end. The first build takes several minutes. The service's Logs tab should show
`Postgres is up.`, `Qdrant is up.` and `Seeded and indexed. Starting the API on port 8080.`

## Put it on a Space

1. Create a Space at huggingface.co, with a PRO account. Choose **Docker** as the SDK, **Blank**
   as the template and **CPU Basic** as the hardware.
2. In the Space's settings, under Variables and secrets, add these as **secrets**:

   | Name | What it is |
   | --- | --- |
   | `GOOGLE_API_KEY` | The Gemini key |
   | `TYPESAFE_API_KEY` | The Jev key |
   | `LANGSMITH_API_KEY` | Only if you want the demo's tickets traced |

3. Clone the Space, copy the files in, and push. Git asks for a password: give a Hugging Face
   access token with write permission, never the account password.

   ```bash
   git clone https://huggingface.co/spaces/YOUR_NAME/deflect ../deflect-space
   python deploy/export_space.py ../deflect-space
   cd ../deflect-space
   git add -A
   git commit -m "Deflect demo"
   git push
   ```

   A Space is its own git repository and its README must begin with the Space's settings. The
   export script copies only what the image is built from, and gives the Space the short README
   in `deploy/SPACE_README.md`. Your GitHub README is not touched.

4. The Space builds the image and starts it. Watch the Logs tab for the same three lines as
   above, with port 7860.

## Settings, on either host

All optional. On Cloud Run add them with `--set-env-vars`, on a Space as **variables**.

| Name | Default | What it does |
| --- | --- | --- |
| `DEFLECT_DAILY_BUDGET_INR` | 25 | Model spend per day, after which no new run starts |
| `DEFLECT_RATE_PER_MINUTE` | 6 | Runs one visitor may start in a minute |
| `DEFLECT_MAX_RPM` | none | A ceiling on requests per minute to Gemini, for a key on a tier that allows few |
| `DEFLECT_TRACE_BACKEND` | none | Set to `langsmith` with the key above |
| `DEFLECT_TRACE_URL` | none | Turns the trace id in the run view into a link, see below |

## What protects the key

Three layers, from the outside in.

1. **A rate limit per visitor.** Six runs a minute by default. It only counts requests that can
   cost a model call. Reading the inbox, a run or the metrics is never limited.
2. **A daily budget.** Every run's model cost is added up, and once the day's total reaches the
   cap, new runs are refused with a message that says why. Everything already in the inbox can
   still be opened. The count lives in memory, so a restart sets it back to zero.
3. **A limit on the key itself.** This is the one that cannot be argued with, and it is yours to
   set. In Google AI Studio, the Spend page of the key's project has a monthly spend cap. Google
   says it takes about ten minutes to apply. The two layers above keep honest visitors inside the
   budget. This one is for everything else.

On Cloud Run, also set a budget with an alert on the billing account. A budget warns and does
not stop anything, which is why `--max-instances 1` matters.

Loading the ten tickets costs about Rs 1.30 at paid tier prices, measured on the same ten
tickets in `results_gemini_jev.json`. A demo that sleeps and wakes loads them again each time.

## What a visitor can and cannot do

- Read every ticket, every step, every tool call. No model call is made for reading.
- Approve or deny the one waiting refund.
- Send a ticket. The console offers five samples, because a made up order id only gets the
  reply that asks for the order id.
- Reset the demo, at most once in thirty minutes.
- Not reach the MCP server. Only the web port is published, and the server speaks to the agent
  over stdio inside the container. If you ever publish the server on its own, give out
  `MCP_HTTP_READONLY_TOKEN` and never the full one.
- Not see anyone's personal details. The console shows placeholders, and in the demo the reply
  endpoint does too.

The demo has no login, so anyone can approve the refund. That is the point of a demo and it is
the reason the data is made up. Outside the demo, set `DEFLECT_API_TOKEN`.

## A link to the trace

Open any trace in LangSmith and copy the address from the browser. Replace the trace id in it
with `{trace_id}`, or with `{trace_uuid}` if the address shows the id with hyphens, and set the
result as `DEFLECT_TRACE_URL`. The run view then links each ticket to its own trace.

A stranger cannot open a private LangSmith project. For the public demo, either leave the
setting empty, so the run view shows the id as text, or share the project publicly from
LangSmith first.

## Your own Postgres or Qdrant

The start script only starts what you did not bring. Set `DATABASE_URL` and it leaves Postgres
alone. Set `QDRANT_URL`, with `QDRANT_API_KEY` for a hosted one, and it leaves Qdrant alone. The
seed runs without a reset, so a database you brought keeps its tickets across restarts.

## Before you show it to anyone

Open it the day before. A demo sleeps, a key can expire, a quota can run out. Send one sample
ticket and approve the refund, then reset.
