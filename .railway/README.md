# CrisisRadar on Railway

`railway.ts` describes the single Streamlit container in the dedicated CrisisRadar project.
The Dockerfile installs Python and Chromium. Firebase remains the persistent database.

Production: https://sensemaker-production.up.railway.app/
Project: `3559f5bf-b605-4dc5-bc1f-e5cdbdc79b4e`; environment: `production`; service: `sensemaker`.
Railway builds the repository root from `Yanguangchen/SenseMaker` on `main`.
Commit and push application changes to deploy. The root `Dockerfile`, `hosted.py`
and `requirements-hosted.txt` must be tracked in Git; local-only files are absent
from GitHub builds. Virtual environments stay on the developer's machine.

Install the infrastructure SDK with `npm ci --prefix .railway` from the repository root.
Node.js 22+ and Railway CLI 5.71.0+ are required for the current IaC variable policy.

```sh
npx @railway/cli login
npx @railway/cli link --project 3559f5bf-b605-4dc5-bc1f-e5cdbdc79b4e --environment production
npx @railway/cli config plan
npx @railway/cli config apply
```

`railway up --service sensemaker --environment production` is available for an
explicit CLI upload, but does not update GitHub or replace committing source changes.

Review the plan before applying. This definition manages the dedicated project's services;
linking it to a different project could propose removing that project's other services.
The `variables.managed: false` policy leaves runtime credentials in Railway Variables.
No credentials belong in this directory. See the repository README for Firebase domain
setup and the runtime variable list. Deploying source does not apply infrastructure changes.

Current configuration format: https://docs.railway.com/infrastructure-as-code
