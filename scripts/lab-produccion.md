# Lab: el despliegue fallido y su rescate

Comandos para copiar y pegar, en el orden del deck (pasos En vivo 01 a 12).
Reemplace los marcadores antes de ejecutar:

| Marcador | Valor |
|---|---|
| `PROJECT_PROD` | Proyecto productivo de la Landing Zone |
| `REGION` | `southamerica-west1` |
| `ZONE` | `southamerica-west1-a` |
| `IMAGE` | Imagen de test-api ya publicada en Artifact Registry |
| `VPC_NETWORK`, `SUBNET`, `SUBNET_RANGE` | Red, subred de Cloud Run y su CIDR |
| `ONPREM_SUBNET` | Subred que simula la red on-prem 10.x |
| `CUSTOMER_ID` | ID de cliente de Google Workspace (`C0...`) |
| `GROUP` | Grupo corporativo, por ejemplo `operaciones@gtd.cl` |
| `PREV` | Revisión estable a la que se vuelve en el rollback |

```bash
export PROJECT_PROD=PROJECT_PROD REGION=southamerica-west1 ZONE=southamerica-west1-a
gcloud config set project "$PROJECT_PROD"
```

Variables de la app usadas en el lab (todas apagadas por defecto):

| Variable | Efecto |
|---|---|
| `REQUIRE_DEMO_API_KEY=true` | `DEMO_API_KEY` vacía detiene el arranque antes de abrir el puerto |
| `LEGACY_URL` | Backend privado que llama `GET /legacy` (timeout 2 s, 503 amigable si falla) |
| `WARMUP_S` | Segundos de conexión simulada a sistemas legacy lentos |
| `WARMUP_MODE` | `blocking`: calienta antes de abrir `$PORT`. `background`: abre el puerto y `/ready` responde 503 hasta terminar |
| `EXPECTED_REGION` | Región que permite la política de la organización en el proyecto (por defecto `us-east1`); el tablero la compara con la región real |

## Tablero en vivo

Abra la URL del servicio en el navegador para ver el tablero: un semáforo en
español, pensado para la audiencia de gerencia, con un veredicto general
("Listo para clientes" / "Aún no está listo") y cinco controles. Cada paso del
lab pasa una luz de rojo a verde, y el encabezado "Versión en producción"
cambia (y se resalta unos segundos) al hacer el rollback del paso 12.

- `curl` a `/` sigue devolviendo JSON; el tablero solo se sirve a navegadores.
- `GET /lab/status` es el JSON detrás del tablero (nunca expone el valor del secreto).
- Defina `EXPECTED_REGION` (por defecto `us-east1`) con la región que permite
  la política de la organización en el proyecto productivo; si no coincide,
  la luz de región queda en rojo.
- Para verlo, el instructor se otorga `roles/run.invoker` con su cuenta del lab
  o, desde el paso 11, acceso por IAP.

## 00:00–00:10 · Escenario (pasos 01–02)

### 01 · test-api tal como corre en el sandbox

```bash
URL_SANDBOX=$(gcloud run services describe test-api-production \
  --project=lorenz-1234 --region=us-east1 --format='value(status.url)')
TOKEN=$(gcloud auth print-identity-token)
curl -s -H "Authorization: Bearer $TOKEN" "$URL_SANDBOX/ready"
```

### 02 · El mismo despliegue en producción: los rechazos

Se ejecuta tal cual se hace en el sandbox. Cada error nombra su constraint.

```bash
gcloud run deploy test-api-production --image=IMAGE \
  --region=us-east1 --allow-unauthenticated
```

## 00:10–00:25 · Colisiones 1 y 2: Org Policy y red (pasos 03–05)

### Colisión 1 · Leer las policies efectivas

```bash
for c in gcp.resourceLocations run.allowedIngress iam.allowedPolicyMemberDomains \
         iam.disableServiceAccountKeyCreation; do
  gcloud org-policies describe "$c" --project="$PROJECT_PROD" --effective
done
```

Si el proyecto del lab no hereda estas reglas, se fijan a nivel de proyecto.
Requiere `roles/orgpolicy.policyAdmin`, que se otorga en la organización.

```bash
cat > locations.yaml <<'EOF'
name: projects/PROJECT_PROD/policies/gcp.resourceLocations
spec:
  rules:
  - values:
      allowedValues:
      - in:southamerica-west1-locations
EOF
cat > domains.yaml <<'EOF'
name: projects/PROJECT_PROD/policies/iam.allowedPolicyMemberDomains
spec:
  rules:
  - values:
      allowedValues:
      - CUSTOMER_ID
EOF
gcloud org-policies set-policy locations.yaml
gcloud org-policies set-policy domains.yaml
```

### 03 · Corregir región, ingress y acceso

```bash
gcloud run deploy test-api-production --image=IMAGE \
  --project="$PROJECT_PROD" --region="$REGION" --ingress=internal \
  --no-allow-unauthenticated \
  --service-account=sa-runtime-production@$PROJECT_PROD.iam.gserviceaccount.com
```

Con `--ingress=internal`, la URL `run.app` solo responde desde la VPC o la red
corporativa: ejecute las pruebas con `call` desde ahí. Si el instructor no
tiene esa ruta, use `--ingress=all` solo mientras prueba y dígalo en voz alta.

```bash
URL_PROD=$(gcloud run services describe test-api-production --region="$REGION" \
  --format='value(status.url)')
TOKEN=$(gcloud auth print-identity-token)
call() { curl -s -w ' %{http_code}\n' -H "Authorization: Bearer $TOKEN" "$URL_PROD$1"; }
call /health
```

### Preparación del instructor · El on-prem simulado

Una VM sin IP externa sirve HTTP en su IP interna, en una subred que representa
la red 10.x de GTD.

```bash
gcloud compute instances create onprem-bd-clientes --zone="$ZONE" \
  --machine-type=e2-micro --subnet=ONPREM_SUBNET --no-address \
  --no-service-account --no-scopes --tags=onprem \
  --image-family=debian-12 --image-project=debian-cloud \
  --metadata=startup-script='#! /bin/bash
mkdir -p /srv && echo "hello from on-prem" > /srv/index.html
cd /srv && python3 -m http.server 80'

ONPREM_IP=$(gcloud compute instances describe onprem-bd-clientes --zone="$ZONE" \
  --format='get(networkInterfaces[0].networkIP)')
```

La app ya apunta al nombre corporativo. Sin ruta, `/legacy` responde un 503
amigable y el error queda en Cloud Logging como JSON:

```bash
gcloud run services update test-api-production --region="$REGION" \
  --update-env-vars=LEGACY_URL=http://bd-clientes.gtd.corp/
call /legacy   # {"error":"legacy backend unavailable"} 503
```

### 04 · Direct VPC egress hacia la red 10.x

```bash
gcloud run services update test-api-production --region="$REGION" \
  --network=VPC_NETWORK --subnet=SUBNET --vpc-egress=private-ranges-only
```

### 05 · Resolver bd-clientes.gtd.corp desde la VPC

En GTD real se usa una zona de reenvío al DNS on-prem
(`--forwarding-targets=DNS_ONPREM_IP`, como en el deck). En el lab, una zona
privada con un registro A cumple el mismo papel:

```bash
gcloud dns managed-zones create gtd-corp --dns-name="gtd.corp." \
  --visibility=private --networks=VPC_NETWORK \
  --description="Lab: simulated gtd.corp"
gcloud dns record-sets create bd-clientes.gtd.corp. --zone=gtd-corp \
  --type=A --ttl=60 --rrdatas="$ONPREM_IP"
```

El segundo firewall: el "on-prem" debe aceptar el CIDR de la subred de Cloud Run.

```bash
gcloud compute firewall-rules create allow-run-to-onprem \
  --network=VPC_NETWORK --direction=INGRESS --allow=tcp:80 \
  --source-ranges=SUBNET_RANGE --target-tags=onprem
call /legacy   # {"status":"ok","legacy":"hello from on-prem\n"} 200
```

## 00:25–00:40 · Colisión 3: arranque y secretos (pasos 06–08)

### 06 · El despliegue que nunca queda Ready

Tres sistemas lentos en serie antes de abrir el puerto, el secreto exigido y
una startup probe corta (30 s en lugar de los 240 s por defecto):

```bash
gcloud run services update test-api-production --region="$REGION" \
  --update-env-vars=REQUIRE_DEMO_API_KEY=true,WARMUP_S=90,WARMUP_MODE=blocking \
  --startup-probe=tcpSocket.port=8080,periodSeconds=10,failureThreshold=3,timeoutSeconds=1
```

La revisión nueva no queda Ready y el tráfico sigue en la anterior.
Diagnóstico en Cloud Logging:

```bash
gcloud run revisions list --service=test-api-production --region="$REGION"
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="test-api-production" AND severity>=ERROR' \
  --freshness=15m --limit=20
```

Primer error: `DEMO_API_KEY is required but empty` y `Application startup
failed`. La app nunca imprime el valor del secreto.

### 07 · secretAccessor y --set-secrets

Hoy `roles/secretmanager.secretAccessor` lo tiene el deployer, no la identidad
de runtime. Se otorga a la SA de runtime y se monta el secreto:

```bash
gcloud secrets add-iam-policy-binding demo-api-key \
  --role=roles/secretmanager.secretAccessor \
  --member=serviceAccount:sa-runtime-production@$PROJECT_PROD.iam.gserviceaccount.com
gcloud run services update test-api-production --region="$REGION" \
  --set-secrets=DEMO_API_KEY=demo-api-key:latest
```

La revisión sigue sin quedar Ready. Segundo error en Cloud Logging:
`The user-provided container failed to start and listen on the port defined
provided by the PORT=8080 environment variable`. El warmup bloqueante retiene
el puerto 90 s y la probe se rinde a los 30 s.

### 08 · /health responde mientras calienta

```bash
gcloud run services update test-api-production --region="$REGION" \
  --update-env-vars=WARMUP_MODE=background
call /health   # 200 al instante
call /ready    # 503 "warming up" durante 90 s, luego 200
```

## 00:40–00:55 · Colisión 4: acceso con IAP (pasos 09–11)

### 09 · Quitar allUsers y activar --iap

Compruebe que no quede `allUsers` (el paso 02 lo intentó). Si aparece, se quita:

```bash
gcloud run services get-iam-policy test-api-production --region="$REGION"
gcloud run services remove-iam-policy-binding test-api-production \
  --region="$REGION" --member=allUsers --role=roles/run.invoker
gcloud run services update test-api-production --region="$REGION" --iap
```

### 10 · El agente de servicio de IAP invoca el servicio

```bash
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_PROD" --format='value(projectNumber)')
gcloud run services add-iam-policy-binding test-api-production \
  --region="$REGION" --role=roles/run.invoker \
  --member=serviceAccount:service-$PROJECT_NUMBER@gcp-sa-iap.iam.gserviceaccount.com
```

Si el agente aún no existe: `gcloud beta services identity create
--service=iap.googleapis.com` (requiere el componente beta).

### 11 · Solo pasa el grupo corporativo: 403 contra OK

```bash
gcloud iap web add-iam-policy-binding --resource-type=cloud-run \
  --service=test-api-production --region="$REGION" \
  --member=group:GROUP --role=roles/iap.httpsResourceAccessor
```

Abra la URL en el navegador: con una cuenta personal, 403; con una cuenta del
grupo, la respuesta de `/`. Elija el ingress a propósito: con `internal`, el
navegador debe estar en la red corporativa o en la VPC. Si la demo se hace
desde internet, el ingress tiene que ser `all` y `run.allowedIngress` debe
permitirlo en el proyecto del lab; IAP es entonces el único control de acceso.

## 00:55–01:00 · Rollback (paso 12)

```bash
gcloud run revisions list --service=test-api-production --region="$REGION"
gcloud run services update-traffic test-api-production --region="$REGION" \
  --to-revisions=PREV=100
```

Cada comando del lab termina en Git: el pipeline aplica
`deploy/cloudrun/service-production.yaml` con `replace` y revierte cualquier
cambio manual.
