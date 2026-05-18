#!/usr/bin/env bash
# Deploy website/ to the gh-pages branch on GitHub.
# Run from the project root: bash scripts/deploy_website.sh

set -euo pipefail

REPO="git@github.com:mdindoost/gsa-gateway.git"
WEBSITE_DIR="$(cd "$(dirname "$0")/../website" && pwd)"
DEPLOY_DIR="/tmp/gsa-gateway-deploy"

echo "Deploying website to GitHub Pages..."

rm -rf "$DEPLOY_DIR"
mkdir "$DEPLOY_DIR"
cp -r "$WEBSITE_DIR/." "$DEPLOY_DIR/"

cd "$DEPLOY_DIR"
git init -q
git checkout -b gh-pages
git add .
git commit -q -m "Deploy website $(date '+%Y-%m-%d %H:%M')"
git remote add origin "$REPO"
git push origin gh-pages --force

echo "Done. Live at: https://mdindoost.github.io/gsa-gateway/"
