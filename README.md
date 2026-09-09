# LinguaCloud — GCP Edition

Same project as the AWS version, rebuilt on Google Cloud, with the raw
backend URLs hidden from the browser via Firebase Hosting.

## Service mapping (AWS → GCP)

| AWS                  | GCP                              |
|-----------------------|-----------------------------------|
| Amazon Translate       | Cloud Translation API (Advanced, v3) |
| AWS Lambda              | Cloud Functions (2nd gen)        |
| API Gateway             | Firebase Hosting rewrites        |
| S3                      | Cloud Storage (GCS)               |
| DynamoDB                | Firestore (Native mode)           |

## Why Firebase Hosting is in here

Calling a Cloud Function's URL directly from browser JavaScript means anyone
can open dev tools → Network tab and see (and call) `https://translatetext-
xxxxx-uc.a.run.app` directly. Firebase Hosting sits in front of the
functions: the frontend calls `/api/translate` (same origin as the page
itself), and Hosting silently forwards that request to the real Cloud
Function server-side. The browser never learns the actual function URL —
functionally the same role API Gateway plays on the AWS side.

## Project structure

```
gcp-project/
├── functions/
│   ├── main.py            # all 3 functions: translate_text, translate_document, get_history
│   └── requirements.txt
├── frontend/
│   └── index.html          # calls /api/translate, /api/translate-document, /api/history
├── firebase.json            # rewrites mapping /api/* to the Cloud Functions
├── deploy.sh                 # one-shot deploy script
└── README.md
```

## Prerequisites

- GCP account with billing enabled (Cloud Translation Advanced requires
  billing even within the free trial)
- `gcloud` CLI installed and authenticated (`gcloud init`)
- Node.js + npm installed (needed for the Firebase CLI — `deploy.sh`
  installs `firebase-tools` automatically if missing)

## Deploying

```bash
cd gcp-project
chmod +x deploy.sh
./deploy.sh
```

This will:
1. Enable required APIs (Cloud Functions, Cloud Build, Translation,
   Firestore, Storage, Cloud Run, Firebase Hosting)
2. Create a Firestore database (Native mode)
3. Create a GCS bucket for translated documents
4. Deploy all three Cloud Functions
5. Install the Firebase CLI if needed, log you in (opens a browser), link
   this GCP project to Firebase, and deploy Hosting with the `/api/*`
   rewrites
6. Print your live app URL: `https://<project-id>.web.app`

Takes roughly 6–10 minutes total, mostly first-time API enablement and the
Firebase CLI install.

## Running the demo

Just open the printed URL — `https://<project-id>.web.app` — in a browser.
No config fields to fill in; the frontend already knows to call `/api/...`
paths on its own origin.

To verify the URLs really are hidden: open dev tools → Network tab, run a
translation, and check the request URL. It should show your own
`web.app` domain, not `*.run.app` or `*.cloudfunctions.net`.

## A gotcha to know about: Firestore composite index

The history query filters by `userId` and orders by `timestamp` on a
different field, which needs a Firestore composite index. The first
`getHistory` call may fail with an error containing a direct link to
auto-create it — click it, wait ~1 minute, then retry. Only happens once.

To pre-create it via CLI instead:
```bash
gcloud firestore indexes composite create \
  --collection-group=translations \
  --field-config=field-path=userId,order=ascending \
  --field-config=field-path=timestamp,order=descending
```

## Cost notes

- New GCP accounts get $300 free credit valid 90 days.
- Cloud Translation Advanced (v3) is billed per character, drawn from that
  credit — same billing model as AWS.
- Cloud Functions, Firestore, Cloud Storage, and Firebase Hosting all have
  generous always-free tiers on top of the trial credit.

## Cleaning up

```bash
gcloud functions delete translateText --region=us-central1 --gen2
gcloud functions delete translateDocument --region=us-central1 --gen2
gcloud functions delete getHistory --region=us-central1 --gen2
gsutil rm -r gs://translation-docs-<your-project-id>
firebase hosting:disable --project <your-project-id>
```
