# Test payloads for the ZMRO_RECORD OData service

Sample bodies for creating a maintenance record in the Z table, taken
verbatim from real rows in the HANA schema so the shapes and lengths are
realistic.

| File | For |
|------|-----|
| `zmro_record_v2_create.json` | OData **V2** (SEGW / `/sap/opu/odata/sap/...`) |
| `zmro_record_v4_create.json` | OData **V4** (RAP / `/sap/opu/odata4/sap/...`) |

---

## Before you send: check the property names

These files use the camelCase names SEGW proposes when you import the
DDIC structure (`RECORD_ID` → `RecordId`). That is a *proposal*, not a
rule — whoever built the service may have kept the underscored names or
renamed them.

Open `$metadata` and match it exactly:

```
GET /sap/opu/odata/sap/ZMRO_SRV/$metadata
```

| DDIC field | SEGW default property |
|------------|----------------------|
| `RECORD_ID` | `RecordId` |
| `AIRCRAFT_REG` | `AircraftReg` |
| `COMPONENT` | `Component` |
| `FINDING` | `Finding` |
| `SEVERITY` | `Severity` |
| `LOCATION` | `Location` |
| `RECOMMENDED_ACTION` | `RecommendedAction` |
| `TECHNICIAN` | `Technician` |
| `TECHNICIAN_USER_ID` | `TechnicianUserId` |
| `INSPECTION_TS` | `InspectionTs` |
| `CREATED_AT` | `CreatedAt` |

---

## Why there is no STATUS field

By design. A record can only be posted to SAP once it is already
`COMPLETE` — `post_record_to_sap` refuses anything else with a 409, and
flips the record to `CLOSED` only after the upload succeeds. So every
record SAP ever receives has the same status by construction, and a
column holding one constant value is noise.

Status stays on the HANA side, where it means something: it drives the
supervisor's `OPEN` → `COMPLETE` → `CLOSED` workflow.

If SAP later needs to know *when* a finding was posted, add a posting
timestamp — that is the question status was standing in for.

---

## The timestamps

`INSPECTION_TS` and `CREATED_AT` are `TIMESTAMPL` — `DEC 21,7`, which
surfaces in OData as **`Edm.Decimal`**. The wire format is
`YYYYMMDDhhmmss.fffffff`, always UTC:

```
2026-08-05 16:43:58.783540 UTC  ->  "20260805164358.7835400"
```

The source is Python `datetime`, which carries only microseconds, so the
7th decimal is always `0`. That is expected, not a truncation.

**Send them as JSON strings, not numbers.** A 21-digit decimal does not
fit in an IEEE-754 double, so an unquoted `20260804104618.8517470` gets
silently rounded by most JSON parsers and the timestamp arrives wrong.
V2 always serialises `Edm.Decimal` as a string; V4 requires it once the
service is called with:

```
Content-Type: application/json;odata.metadata=minimal;IEEE754Compatible=true
```

### If the service models them as dates instead

Some services expose the timestamps as a date type rather than a
decimal. Then the same instant looks like this:

| Model | JSON |
|-------|------|
| V2 `Edm.DateTime` | `"\/Date(1785948238783)\/"` (epoch ms, sub-ms lost) |
| V4 `Edm.DateTimeOffset` | `"2026-08-05T16:43:58.7835400Z"` |
| DDIC `UTCLONG` in RAP | `"2026-08-05T16:43:58.7835400Z"` |

`UTCLONG` + `Edm.DateTimeOffset` is the cleanest of these — readable on
the wire, no precision trap, and no conversion needed at either end.

---

## Sending it (OData V2, CSRF)

A modifying V2 call needs a CSRF token plus the session cookies from the
same conversation.

```bash
# 1. Fetch the token and cookies
curl -i -u "$SAP_USER:$SAP_PASS" \
  -H "X-CSRF-Token: Fetch" \
  -c cookies.txt \
  "https://<host>/sap/opu/odata/sap/ZMRO_SRV/ZMRO_RECORDSet?\$top=1"

# 2. POST with the token from the response header
curl -i -u "$SAP_USER:$SAP_PASS" \
  -H "X-CSRF-Token: <token from step 1>" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -b cookies.txt \
  -d @zmro_record_v2_create.json \
  "https://<host>/sap/opu/odata/sap/ZMRO_SRV/ZMRO_RECORDSet"
```

Expect `201 Created` with the created entity echoed back under `d`.

In Postman: the same two steps, and make sure the cookie jar is enabled
— a token without its matching session cookie gets you
`403 CSRF token validation failed`.

The `{"d": {...}}` wrapper is a *response* convention. Post the flat
object as in these files; Gateway accepts the wrapper too, but it isn't
needed.

---

## Several at once ($batch, V2)

Note the blank line between each part's headers and its body — a missing
one is the usual cause of a batch that parses as empty.

```
POST /sap/opu/odata/sap/ZMRO_SRV/$batch HTTP/1.1
Content-Type: multipart/mixed; boundary=batch_1
X-CSRF-Token: <token>

--batch_1
Content-Type: multipart/mixed; boundary=changeset_1

--changeset_1
Content-Type: application/http
Content-Transfer-Encoding: binary

POST ZMRO_RECORDSet HTTP/1.1
Content-Type: application/json

{ ...contents of zmro_record_v2_create.json... }

--changeset_1--

--batch_1--
```

---

## Things the service should reject

Worth testing alongside the happy path:

| Case | Expected |
|------|----------|
| `RecordId` absent or not 36 chars | Rejected — it's the key |
| Same `RecordId` posted twice | `409` / duplicate key, not a silent second row |
| `Finding` of 5000+ characters | Accepted — the column is `STRING` (`NCLOB`), unbounded |
| `InspectionTs` sent as a bare number | Compare what lands in the table against what you sent |
| `AircraftReg` longer than 20 chars | Rejected, not truncated |

The last one matters: the source column is `NVARCHAR(20)` and the Z table
is `CHAR20`, so anything longer is already impossible upstream — but a
truncating service would hide a genuine mapping error later.
