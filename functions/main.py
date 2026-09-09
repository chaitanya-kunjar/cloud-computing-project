"""
Cloud Functions (2nd gen) backend for the GCP version of the translation
system. Mirrors the AWS Lambda implementation:

  translate_text      <-> lambda_translate_text.py
  translate_document  <-> lambda_translate_document.py
  get_history          <-> lambda_get_history.py

Environment variables (set at deploy time, see deploy.sh):
  TRANSLATE_LOCATION   - "global" works for both text and document translation
  DOCS_BUCKET          - GCS bucket name for storing translated documents
"""

import base64
import datetime
import json
import os
import uuid

import functions_framework
from google.cloud import firestore
from google.cloud import storage
from google.cloud import translate_v3 as translate

PROJECT_ID = (
    os.environ.get("PROJECT_ID")
    or os.environ.get("GOOGLE_CLOUD_PROJECT")
    or os.environ.get("GCP_PROJECT")
)
LOCATION = os.environ.get("TRANSLATE_LOCATION", "global")
DOCS_BUCKET = os.environ.get("DOCS_BUCKET")

translate_client = translate.TranslationServiceClient()
firestore_client = firestore.Client()
storage_client = storage.Client()

CORS_HEADERS = {"Access-Control-Allow-Origin": "*"}


def _cors_preflight(request):
    if request.method == "OPTIONS":
        headers = {
            **CORS_HEADERS,
            "Access-Control-Allow-Methods": "POST,GET",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Max-Age": "3600",
        }
        return ("", 204, headers)
    return None


def _json(status_code, body_dict):
    return (json.dumps(body_dict, default=str), status_code, CORS_HEADERS)


# ---------------------------------------------------------------------------
# 1. Real-time text translation
# ---------------------------------------------------------------------------


@functions_framework.http
def translate_text(request):
    pre = _cors_preflight(request)
    if pre:
        return pre

    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    target_lang = body.get("targetLanguageCode")
    source_lang = body.get("sourceLanguageCode")
    user_id = body.get("userId", "guest")

    if not text:
        return _json(400, {"error": "'text' is required"})
    if not target_lang:
        return _json(400, {"error": "'targetLanguageCode' is required"})

    parent = f"projects/{PROJECT_ID}/locations/{LOCATION}"
    req = {
        "parent": parent,
        "contents": [text],
        "mime_type": "text/plain",
        "target_language_code": target_lang,
    }
    if source_lang and source_lang != "auto":
        req["source_language_code"] = source_lang

    try:
        result = translate_client.translate_text(request=req)
    except Exception as e:  # noqa: BLE001
        return _json(500, {"error": str(e)})

    translation = result.translations[0]
    translated_text = translation.translated_text
    detected_source = translation.detected_language_code or source_lang or "auto"

    _save_history(
        {
            "userId": user_id,
            "type": "text",
            "sourceText": text,
            "translatedText": translated_text,
            "sourceLanguageCode": detected_source,
            "targetLanguageCode": target_lang,
        }
    )

    return _json(
        200,
        {
            "sourceText": text,
            "translatedText": translated_text,
            "sourceLanguageCode": detected_source,
            "targetLanguageCode": target_lang,
        },
    )


# ---------------------------------------------------------------------------
# 2. Document translation (Cloud Translation Advanced)
# ---------------------------------------------------------------------------

ALLOWED_MIME_TYPES = {
    "text/plain",
    "text/html",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


@functions_framework.http
def translate_document(request):
    pre = _cors_preflight(request)
    if pre:
        return pre

    body = request.get_json(silent=True) or {}
    file_name = body.get("fileName", "document.txt")
    mime_type = body.get("mimeType", "text/plain")
    source_lang = body.get("sourceLanguageCode")
    target_lang = body.get("targetLanguageCode")
    user_id = body.get("userId", "guest")
    file_b64 = body.get("fileContentBase64")

    if not file_b64:
        return _json(400, {"error": "'fileContentBase64' is required"})
    if not target_lang:
        return _json(400, {"error": "'targetLanguageCode' is required"})
    if mime_type not in ALLOWED_MIME_TYPES:
        return _json(
            400,
            {"error": f"Unsupported mimeType. Allowed: {sorted(ALLOWED_MIME_TYPES)}"},
        )

    try:
        file_bytes = base64.b64decode(file_b64)
    except Exception:  # noqa: BLE001
        return _json(400, {"error": "fileContentBase64 is not valid base64"})

    parent = f"projects/{PROJECT_ID}/locations/{LOCATION}"
    doc_input = translate.DocumentInputConfig(content=file_bytes, mime_type=mime_type)

    req = {
        "parent": parent,
        "target_language_code": target_lang,
        "document_input_config": doc_input,
    }
    if source_lang and source_lang != "auto":
        req["source_language_code"] = source_lang

    try:
        response = translate_client.translate_document(request=req)
    except Exception as e:  # noqa: BLE001
        return _json(500, {"error": str(e)})

    translated_bytes = response.document_translation.byte_stream_outputs[0]
    translated_b64 = base64.b64encode(translated_bytes).decode("utf-8")

    gcs_path = None
    if DOCS_BUCKET:
        try:
            bucket = storage_client.bucket(DOCS_BUCKET)
            blob_name = f"translated/{uuid.uuid4()}_{file_name}"
            blob = bucket.blob(blob_name)
            blob.upload_from_string(translated_bytes, content_type=mime_type)
            gcs_path = f"gs://{DOCS_BUCKET}/{blob_name}"
        except Exception:  # noqa: BLE001
            gcs_path = None

    _save_history(
        {
            "userId": user_id,
            "type": "document",
            "fileName": file_name,
            "sourceLanguageCode": source_lang or "auto",
            "targetLanguageCode": target_lang,
            "gcsPath": gcs_path or "",
        }
    )

    return _json(
        200,
        {
            "fileName": file_name,
            "translatedContentBase64": translated_b64,
            "mimeType": mime_type,
            "gcsPath": gcs_path,
        },
    )


# ---------------------------------------------------------------------------
# 3. Translation history
# ---------------------------------------------------------------------------


@functions_framework.http
def get_history(request):
    pre = _cors_preflight(request)
    if pre:
        return pre

    user_id = request.args.get("userId", "guest")
    limit = int(request.args.get("limit", 20))

    try:
        docs = (
            firestore_client.collection("translations")
            .where("userId", "==", user_id)
            .order_by("timestamp", direction=firestore.Query.DESCENDING)
            .limit(limit)
            .stream()
        )
        items = [d.to_dict() for d in docs]
    except Exception as e:  # noqa: BLE001
        return _json(500, {"error": str(e)})

    return _json(200, {"items": items})


def _save_history(fields: dict):
    try:
        fields["timestamp"] = datetime.datetime.utcnow().isoformat()
        firestore_client.collection("translations").add(fields)
    except Exception:  # noqa: BLE001
        pass
