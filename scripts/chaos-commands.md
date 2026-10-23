# SLO lab: chaos commands

Run `scripts/load-checkout.sh` in another terminal first.

## 1. Latency incident (burns the latency budget)

```bash
gcloud run services update test-api-production --region us-east1 \
  --project lorenz-1234 \
  --update-env-vars=SLOW_RATE=0.5,SLOW_MS=1500,ERROR_RATE=0
```

## 2. Availability incident (burns the availability budget)

```bash
gcloud run services update test-api-production --region us-east1 \
  --project lorenz-1234 \
  --update-env-vars=SLOW_RATE=0,ERROR_RATE=0.05
```

## 3. Recover (back to healthy)

```bash
gcloud run services update test-api-production --region us-east1 \
  --project lorenz-1234 \
  --remove-env-vars=SLOW_RATE,SLOW_MS,ERROR_RATE
```

## Manual log-based metric (Logs Explorer)

Paste in Logging > Logs Explorer, then Actions > Create metric (Counter).

```
resource.type="cloud_run_revision"
resource.labels.service_name="test-api-production"
jsonPayload.message="request completed"
jsonPayload.status>=500
```
