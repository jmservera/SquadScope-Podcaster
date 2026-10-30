"""Explicit offline human attestations for approved-script test fixtures."""


def provider_authorization_for(manifest):
    generation = manifest["generation"]
    return {
        "selected_provider": generation.get("provider_settings", {}).get("provider", "openai-tts"),
        "fallback_provider": "azure-speech",
        "provider_selection_reference": "test-evidence/provider-selection",
        "provider_privacy_review_reference": "test-evidence/privacy-review",
        "rai_security_signoff_reference": "test-evidence/rai-security-review",
        "disclosure_reference": "test-evidence/synthetic-voice-disclosure",
        "region": "test-approved-region",
        "provider_settings_sha256": generation.get("provider_settings_sha256", ""),
    }


def approval_evidence_for(manifest):
    return {
        **manifest["review"].get("snapshot", {}),
        "provider_authorization": provider_authorization_for(manifest),
        "review_checks_confirmed": True,
    }
