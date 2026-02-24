# Orchestrator configuration (custom endpoints + important fields)

## 1) Put settings into .env

Copy `configs/.env.sample` to `.env` and edit:

- `GEN_API_URL` : generation endpoint
- `GEN_API_INCLUDE_KEYS` : which top-level keys from testcase are sent to the endpoint
- `GEN_API_RESPONSE_TEXT_KEYS` : which response field contains the generated text
- `AUTO_GENERATE_BASE=1` : if testcase has no `generated_text`, call the API once and populate it

This repo uses a tiny built-in loader. Set `DOTENV_PATH` if your file name is not `.env`.

## 2) Put project-specific important fields into a JSON spec

Set `IMPORTANT_FIELDS_SPEC=path/to/spec.json`.

Supported sections:

- `consistency_terms.paths`: extracted terms used for consistency term presence checks
- `must_mention_terms.paths`: extracted terms that must be mentioned in the output (`accuracy_must_mention_terms`)

Path syntax is a small subset:
- `a.b.c`
- `a.list[].field`

## 3) Lab/Company endpoint example

Request:
- `lab.professors[]` + `company.work_detail` + `config.wording_style`

Response:
- `gen_lab_company_description`

Set:
- `GEN_API_INCLUDE_KEYS=lab,company,config,user_input,last_updated`
- `GEN_API_RESPONSE_TEXT_KEYS=gen_lab_company_description`
