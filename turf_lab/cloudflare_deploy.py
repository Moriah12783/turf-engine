"""Automated Cloudflare Pages deployment module for headless publishing."""

import hashlib
import json
import os
import shutil
import ssl
import subprocess
import urllib.request
import urllib.error
from typing import Any, Dict, Optional


class CloudflarePagesDeployer:
    """Deploys static site assets directly to Cloudflare Pages."""

    def __init__(self, account_id: str = "bfb7a27738f18d7e642980d343a69ee8", project_name: str = "prono-elite-turf", api_token: Optional[str] = None):
        self.account_id = account_id
        self.project_name = project_name
        self.api_token = api_token or os.environ.get("CLOUDFLARE_API_TOKEN")

    @classmethod
    def from_config(cls, config_path: str = "config.json") -> "CloudflarePagesDeployer":
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    return cls(
                        account_id=cfg.get("account_id", "bfb7a27738f18d7e642980d343a69ee8"),
                        project_name=cfg.get("project_name", "prono-elite-turf"),
                        api_token=cfg.get("api_token")
                    )
            except Exception:
                pass
        return cls()

    def sync_local_site_folder(self, html_source_path: str = "benchmark_dashboard.html", site_dir: str = "site") -> str:
        """Ensures site/index.html is always synchronized with benchmark_dashboard.html."""
        os.makedirs(site_dir, exist_ok=True)
        dest_index = os.path.join(site_dir, "index.html")
        if os.path.exists(html_source_path):
            shutil.copyfile(html_source_path, dest_index)
        return dest_index

    def deploy_direct(self, site_dir: str = "site") -> Dict[str, Any]:
        """Deploys index.html to Cloudflare Pages via Direct Upload API or Wrangler."""
        self.sync_local_site_folder("benchmark_dashboard.html", site_dir)

        index_file = os.path.join(site_dir, "index.html")
        if not os.path.exists(index_file):
            return {"success": False, "status": "FILE_NOT_FOUND", "message": f"{index_file} introuvable."}

        if not self.api_token:
            return {
                "success": False,
                "status": "NO_TOKEN",
                "message": "Fichier 'site/index.html' synchronisé localement."
            }

        # 1. Try via Cloudflare Pages Direct Upload API
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        try:
            with open(index_file, "rb") as f:
                content = f.read()
            file_hash = hashlib.sha256(content).hexdigest()

            # Step A: Check and upload missing asset
            upload_url = f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/pages/assets/upload"
            headers_upload = {
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json",
                "User-Agent": "TurfEngine-Deployer/1.0"
            }
            payload_upload = json.dumps([{"key": file_hash, "value": content.decode("utf-8", errors="replace"), "metadata": {"contentType": "text/html"}}]).encode("utf-8")
            req_up = urllib.request.Request(upload_url, data=payload_upload, headers=headers_upload, method="POST")
            try:
                urllib.request.urlopen(req_up, context=ctx, timeout=10)
            except Exception:
                pass

            # Step B: Create deployment with manifest
            deploy_url = f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/pages/projects/{self.project_name}/deployments"
            headers_deploy = {
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json",
                "User-Agent": "TurfEngine-Deployer/1.0"
            }
            manifest_payload = json.dumps({
                "manifest": {"/index.html": file_hash}
            }).encode("utf-8")

            req_dep = urllib.request.Request(deploy_url, data=manifest_payload, headers=headers_deploy, method="POST")
            with urllib.request.urlopen(req_dep, context=ctx, timeout=15) as resp:
                res_json = json.loads(resp.read().decode("utf-8"))
                if res_json.get("success"):
                    return {
                        "success": True,
                        "status": "DEPLOYED",
                        "custom_domain": "https://prono.elite-turf.fr"
                    }
        except Exception as e:
            # Fallback message
            return {
                "success": False,
                "status": "FALLBACK_LOCAL_SYNC",
                "message": f"Fichier local 'site/index.html' synchronisé. ({e})"
            }

        return {"success": False, "status": "PENDING", "message": "site/index.html synchronisé."}
