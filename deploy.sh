#!/bin/bash
# Deploys the full GCP translation system: 3 Cloud Functions, Firestore,
# a GCS bucket, and Firebase Hosting configured to proxy /api/* to the
# functions — so the browser never sees the raw Cloud Function URLs.
#
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh
#
# Prerequisites: gcloud CLI installed + `gcloud init` already run,
# Node.js/npm installed (needed for the Firebase CLI).

set -e

PROJECT_ID=$(gcloud config get-value project)
REGION="us-central1"
BUCKET_NAME="translation-docs-${PROJECT_ID}"

if [ -z "$PROJECT_ID" ]; then
  echo "No active gcloud project. Run 'gcloud init' first."
  exit 1
fi

echo "Project:  $PROJECT_ID"
echo "Region:   $REGION"
echo "Bucket:   $BUCKET_NAME"
echo ""

echo "1/6  Enabling required APIs (first run only, ~1-2 min)..."
gcloud services enable \
  cloudfunctions.googleapis.com \
  cloudbuild.googleapis.com \
  translate.googleapis.com \
  firestore.googleapis.com \
  storage.googleapis.com \
  run.googleapis.com \
  firebasehosting.googleapis.com

echo "2/6  Creating Firestore database (Native mode) if it doesn't exist..."
gcloud firestore databases create --location="$REGION" --type=firestore-native \
  || echo "   (Firestore database likely already exists — continuing)"

echo "3/6  Creating GCS bucket for translated documents..."
gsutil mb -l "$REGION" "gs://${BUCKET_NAME}" \
  || echo "   (Bucket likely already exists — continuing)"

echo "4/6  Deploying Cloud Functions..."
cd functions

gcloud functions deploy translateText \
  --gen2 --runtime=python312 --region="$REGION" \
  --source=. --entry-point=translate_text \
  --trigger-http --allow-unauthenticated \
  --set-env-vars=TRANSLATE_LOCATION=global,DOCS_BUCKET="$BUCKET_NAME"

gcloud functions deploy translateDocument \
  --gen2 --runtime=python312 --region="$REGION" --memory=512MB --timeout=60s \
  --source=. --entry-point=translate_document \
  --trigger-http --allow-unauthenticated \
  --set-env-vars=TRANSLATE_LOCATION=global,DOCS_BUCKET="$BUCKET_NAME"

gcloud functions deploy getHistory \
  --gen2 --runtime=python312 --region="$REGION" \
  --source=. --entry-point=get_history \
  --trigger-http --allow-unauthenticated \
  --set-env-vars=TRANSLATE_LOCATION=global,DOCS_BUCKET="$BUCKET_NAME"

cd ..

echo "5/6  Setting up Firebase Hosting to front the app..."
if ! command -v firebase &> /dev/null; then
  echo "   Firebase CLI not found — installing (requires Node.js/npm)..."
  npm install -g firebase-tools
fi

echo "   You may be prompted to log in to Firebase — a browser window will open."
firebase login --no-localhost || firebase login

# Registers this GCP project as a Firebase project too (safe to re-run).
firebase projects:addfirebase "$PROJECT_ID" 2>/dev/null || true

echo '{ "projects": { "default": "'"$PROJECT_ID"'" } }' > .firebaserc

firebase deploy --only hosting --project "$PROJECT_ID"

echo ""
echo "6/6  Done."
echo ""
echo "Your app is live at: https://${PROJECT_ID}.web.app"
echo "The frontend calls relative paths like /api/translate — visitors"
echo "never see the raw Cloud Function URLs (check the browser Network tab"
echo "to confirm: requests show your own domain, not *.run.app)."
