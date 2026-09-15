# Quiz bank and TAO spike

Discovery spike for the "native customizable quiz-building similar to QBank" ask. It sits beside
docs/LTI-EXPLAINED.md and reads the same way. Everything in it was checked on 2026-09-15 against
the cplatform sandbox at http://localhost:3100 (instructure/canvas-lms master snapshot, source at
D:\Canvas\test\canvas\cplatform) or against the cited URL. The exact calls are in the appendix.

## Summary

Canvas classic quizzes have a full REST API in this sandbox and the parallel workstream already
builds quizzes from a Redshift pool through it. That is the path the six-week team can commit to.

New Quizzes is not in this sandbox and cannot be. Its item banks and item editor live in
Instructure's hosted quiz-lti service. The Canvas source only carries the launch shell, the
feature flags, and one list endpoint. Nothing in the API docs of this build creates a New Quizzes
item or bank.

Canvas exports quizzes as QTI 1.2, not QTI 2.1. TAO imports QTI 2.1 and 2.2. So the two do not
connect through a zip file without a conversion step. Canvas itself needs a separate Python tool
(QTIMigrationTool) to read QTI 1.2 on import, and that tool is absent from the sandbox container.

TAO has a free Community Edition under AGPLv3, published as a Docker Compose stack in December
2025. The stack is six containers and 15 GB of images. On this laptop it pulled in 5.5 minutes
and its portal answered within 22 minutes, but I stopped it before trying an import, so the REST
import is unproven here. Nobody has confirmed TAO credentials or an instance on Kaplan's side.

## New Quizzes and classic quizzes in this sandbox

Two quiz engines share the Canvas UI. Classic quizzes are Rails models in this repo. New Quizzes
is an LTI tool Instructure hosts, registered in Canvas with `tool_id` "Quizzes 2"
(app/models/context_external_tool.rb:167, `QUIZ_LTI = "Quizzes 2"`). A course has New Quizzes
only when the `quizzes_next` feature flag is on and that tool is installed
(app/helpers/new_quizzes_features_helper.rb:44, `new_quizzes_enabled?`).

### What the sandbox has

- `GET /api/v1/accounts/1/features/enabled` returns 68 flags and `quizzes_next` is not among them. `GET /api/v1/courses/1/features/enabled` has one quiz-related flag, `outcome_alignment_summary_with_new_quizzes`.
- `quizzes_next` is defined as `state: allowed` with `visible_on: quizzes_next_visible_on_hook` (config/feature_flags/00_standard.yml:281). The hook returns true only when the root account has `settings[:provision]["lti"]` (lib/feature_flags/hooks.rb:78), which Instructure's provisioner writes. The sandbox has no such setting, so the flag is not even visible in the admin UI.
- `GET /api/v1/accounts/1/external_tools` lists one tool, id 6, Performance Dashboard. No "Quizzes 2" tool exists, so `Course#quiz_lti_tool` (app/models/course.rb:4701) returns nil.
- The New Quizzes pages are a shell. `/courses/:course_id/quizzes/:id/build`, `/banks/*` and friends (config/routes.rb:358-377) go to app/controllers/new_quizzes_controller.rb, which refuses without the tool and otherwise loads `remoteEntry.js` from a CloudFront host named in DynamicSettings `new_quizzes.yml` (lib/services/new_quizzes.rb:43-98). The sandbox has no such settings.
- The only New Quizzes REST endpoint in the repo is `GET /api/v1/courses/:course_id/all_quizzes` (app/controllers/quizzes_next/quizzes_api_controller.rb), a combined list. Greps across app, lib and config: `quiz_lti` 148 files, `new_quizzes` 155, `quizzes_next` 90, `NewQuizzesFeaturesController` 0, `new_quizzes_item_banks` 0.
- `POST /api/v1/courses/:course_id/content_exports` accepts `export_type=quizzes2` (app/controllers/content_exports_api_controller.rb:155), a push to the quiz-lti tool. Without the tool it has nowhere to send it.

So New Quizzes item banks are hosted only. On Kaplan's production Canvas they exist when the
account is provisioned and the flag is on. Building or reading items there means the quiz-lti
service's own API, which is not in this repo and needs Kaplan's Canvas admin.

### Classic quizzes, which work here

All of these answered on the sandbox with the admin token.

- `GET /api/v1/courses/1/quizzes` returned 2 quizzes, ids 1 and 2, both `quiz_type` assignment with 3 questions each.
- `POST /api/v1/courses/:course_id/quizzes` creates a quiz with `quiz[title]`, `quiz[quiz_type]`, `quiz[published]`, attempts, time limit and the rest (app/controllers/quizzes/quizzes_api_controller.rb:361-481). `PUT` and `DELETE` on `/quizzes/:id` edit and remove it. `POST /quizzes/:id/reorder` orders items.
- `POST /api/v1/courses/:course_id/quizzes/:quiz_id/questions` creates a question with `question[question_type]` from twelve types (multiple_choice_question, multiple_answers_question, true_false_question, short_answer_question, fill_in_multiple_blanks_question, multiple_dropdowns_question, matching_question, numerical_question, calculated_question, essay_question, file_upload_question, text_only_question), `question[question_text]`, `question[points_possible]` and `question[answers]` (app/controllers/quizzes/quiz_questions_controller.rb:264-299). `GET /quizzes/1/questions` returned the three multiple_choice_question rows Q1 to Q3.
- Question banks are read only over REST. `GET /api/v1/question_banks?context_type=Course&context_id=1&include_question_count=true` returned bank 1, "Unfiled Questions", 6 questions. `GET /api/v1/question_banks/:id` and `GET /api/v1/question_banks/:id/questions` are the other two (app/controllers/assessment_question_banks_controller.rb:234-293). There is no create, update, or move endpoint under `@API`. app/controllers/question_banks_controller.rb is the UI controller and has none.
- Canvas has no item-level tags on classic questions. The bank is a flat list. That is why this POC keeps topic and difficulty in Redshift `nudges.content_items` and the parallel workstream keeps them in `nudges.question_pool`.

### What the MCP server wraps

D:\Canvas\test\canvas\cmcp exposes 107 tools in tools/TOOL_MANIFEST.json. `add_module_item`
accepts `item_type=Quiz` with a `content_id`. `create_content_migration` hardcodes
`migration_type=course_copy_importer` (src/canvas_mcp/tools/content_migrations.py:21). No tool
creates a quiz, a question, or a bank. The parallel workstream adds `create_quiz_from_pool`,
which takes supplied questions and creates a classic quiz. That tool is the sandbox-proven path.

## QTI export and import

QTI is the 1EdTech interchange format for questions and tests. Canvas and TAO write different
versions of it.

### The export, as run

`POST /api/v1/courses/1/content_exports` with `export_type=qti` and `select[quizzes][]=1` at
19:46:10Z answered in 7.5 s with export id 1, `workflow_state` created, and a `progress_url`. A
poll at 19:46:42Z said exporting. A poll at 19:48:06Z said exported with an attachment created at
19:46:51Z, so the job itself took about 34 s. The download at 19:48:43Z took 7.3 s.

The file is docs/sat101-qti-export.zip, 3623 bytes, untracked. It holds four entries:

| Entry | Bytes |
| --- | --- |
| imsmanifest.xml | 2105 |
| g4b927ea0ad2ea69259ee815b178a3960/assessment_meta.xml | 3615 |
| g4b927ea0ad2ea69259ee815b178a3960/g4b927ea0ad2ea69259ee815b178a3960.xml | 6849 |
| non_cc_assessments/ | 0 |

assessment_meta.xml is Canvas's own `cccv1p0` quiz settings (title, attempts, scoring policy).
The second XML is the assessment. Its root is `questestinterop` in namespace
`http://www.imsglobal.org/xsd/ims_qtiasiv1p2`. That is QTI 1.2, written by
lib/cc/qti/qti_generator.rb:277. It carries the three items with `question_type`,
`points_possible` and `original_answer_ids` as Canvas metadata fields.

### Import into Canvas

`GET /api/v1/courses/1/content_migrations/migrators` lists six migrators, and `qti_converter`
("QTI .zip file") is one. `POST /api/v1/courses/:course_id/content_migrations` with
`migration_type=qti_converter` and a `pre_attachment` upload or `settings[file_url]` runs it
(app/controllers/content_migrations_controller.rb:249-402). `settings[question_bank_name]` or
`settings[question_bank_id]` puts the imported questions into a bank, and
`settings[insert_into_module_id]` places the quiz in a module.

The converter accepts QTI 2.0 and 2.1 packages as they are
(gems/plugins/qti_exporter/lib/qti/converter.rb:31-36 and :53-57). A QTI 1.2 package first goes
through Instructure's Python QTIMigrationTool, which Canvas looks for at
vendor/QTIMigrationTool/migrate.py (gems/plugins/qti_exporter/lib/qti.rb:24-34). The
cplatform-web-1 container has no such directory and `Qti.migration_executable` is nil there, so
a QTI 1.2 import in this sandbox raises "Couldn't find QTI Migration Tool" (qti.rb:127-130).
Hosted Canvas ships the tool. I did not run an import, because it would add a duplicate quiz to
course 1 while the parallel workstream is working in it.

The practical reading is that a Canvas QTI 1.2 zip is an interchange format between Canvas
instances, and a QTI 2.1 zip is the format that both Canvas import and TAO accept.

## TAO

### What it is

TAO is an open source assessment platform. The University of Luxembourg started it and Open
Assessment Technologies (OAT) in Luxembourg maintains it (`https://github.com/oat-sa/package-tao`).
OAT sells four editions on `https://www.taotesting.com/products/`. Community Edition is free.
Accelerate starts at 499 EUR a month, Ignite at 990 EUR a month, and Enterprise is custom
pricing with private hosting and multi-tenancy. The comparison table lists "Import/Export QTI
Items/tests" and "LMS Delivery via LTI" as features.

Two generations exist. The legacy 3.x line is a PHP monolith built with Composer from
`https://github.com/oat-sa/package-tao` (last release 3.6.0, 2022-12-21). TAO Community Edition
was announced on 2025-12-05 under AGPLv3
(`https://www.taotesting.com/blog/oat-launches-tao-community-edition/`) and lives at
`https://github.com/tao-ce/tao-ce` (release 2025.10-v1.5, 2025-12-17).

### QTI support

The legacy REST export describes itself as "Exports an existing QTI Item as a QTI 2.1 package"
(`https://github.com/oat-sa/extension-tao-itemqti/blob/master/doc/rest.json`). OAT's own article
says TAO "uses standard QTI 2.1 as the main item model"
(`https://github.com/oat-sa/taohub-articles/blob/master/forge/QTI-in-TAO.md`). Community forum
posts date QTI 2.2 export to TAO 3.2. OAT co-chaired the QTI 3 work at 1EdTech
(`https://www.taotesting.com/blog/the-process-of-developing-qti-3-in-the-ims-qti-working-group/`),
but I found no primary page that says the Community Edition imports QTI 3 packages. Treat QTI
2.1 as the safe interchange version.

### REST endpoints

The legacy item API is declared in extension-tao-itemqti's `doc/rest.json`. All paths are
relative to the TAO root URL.

- `POST /taoQtiItem/RestQtiItem/import/` with multipart `content` (the zip), optional `class-uri` or `class-label`, and flags `itemMustExist`, `itemMustBeOverwritten`, `overwriteByLabelInTargetClass`, `enableMetadataGuardians`, `enableMetadataValidators`, `metadataRequired`.
- `POST /taoQtiItem/RestQtiItem/importDeferred/` with the same fields, asynchronous, and `GET /taoQtiItem/RestQtiItem/getStatus?id=` to poll.
- `GET /taoQtiItem/RestQtiItem/export/?id=` returns a QTI 2.1 package.
- `POST /taoQtiItem/RestQtiItem/createQtiItem/`, `POST /taoQtiItem/RestQtiItem/createClass`, `POST /taoQtiItem/RestQtiItem/updateMetadata`.
- Tests. `POST /taoQtiTest/RestQtiTests/import` and `importDeferred`, plus `exportQtiPackage` and `getItems`, from actions/class.RestQtiTests.php in `https://github.com/oat-sa/extension-tao-testqti`.

`rest.json` declares no security scheme. The legacy REST controllers authenticate with HTTP
basic auth against a TAO user. tao-core's `tao_models_classes_HttpBasicAuthAdapter` reads
`PHP_AUTH_USER` and `PHP_AUTH_PW` and calls `LoginService::authenticate`
(`https://github.com/oat-sa/tao-core/blob/master/models/classes/class.HttpBasicAuthAdapter.php`).
The archived extension-tao-restful wiki names `BasicAuthentication` as the default authenticator
(`https://github.com/oat-sa-archived/extension-tao-restful/wiki/Rest-API-v1`). OAT's user guide
says the newer "NextGen" APIs use OAuth 2.0 client credentials, but the page
(`https://userguide.taotesting.com/user-documentation/latest/public/api-authentication`) returned
404 when fetched, so that claim rests on the search snippet only. Whether the 2025 Community
Edition still serves the `/taoQtiItem/RestQtiItem/` routes is something only a running instance
answers.

### Docker images

- Official. `https://github.com/tao-ce/tao-ce/blob/main/INSTALL.md` installs with `docker compose -f docker-compose.tao-ce.yaml up -d` from a gist. The image is `tao.docker.scarf.sh/tao-ce/tao-ce:latest`. It runs privileged with `/sys/fs/cgroup` mounted, publishes 443 only, expects the hostname `community.tao.internal`, needs 4 GB for Docker Desktop, and depends on Elasticsearch 8.17.4, Postgres 17, Valkey 8, and pubsub and firestore emulators from `quay.io/tao-ce/services/`. Default login is admin with password `password`. The vendor download page sits behind a form, but INSTALL.md carries the same compose file without one.
- Legacy. `https://github.com/oat-sa/package-tao/tree/master/docker` holds nginx and phpfpm Dockerfiles to build yourself. No image under an oat-sa name exists on Docker Hub.
- Community. `devsu/tao` on Docker Hub (tags latest and 3.4-rc01, 2021-07-13, 339 MB) builds TAO 3.4, needs an external database, and finishes the install in the browser. `taotesting/tao-release` (2024-11-28) is a CI image and its page says it is deprecated in favor of Community Edition. `t1mm/tao-apache-ubuntu` is a 2018 TAO 3.2 RC.

### Local attempt

The box was 30 minutes from the first docker command, 19:50:22Z to 20:20:22Z. The compose file
is docs/tao-ce.docker-compose.yaml, the gist file with the tao service renamed `canvas-tao`,
labelled `project=canvas`, and published on 3200 instead of 443.

- Pull ran 19:50:28Z to 19:55:51Z. Six images, 15.3 GB, of which tao-ce is 9.03 GB.
- `up -d` finished 19:56:37Z and `canvas-tao` was running at 19:56:38Z. It is a systemd container (`/sbin/init`) that runs about 30 `tao-ce.*` units inside.
- My readiness probe was wrong. It called `https://localhost:3200/` with a `Host: community.tao.internal` header every 3 s for 1200 s and never connected. The TLS front end selects by SNI, and a Host header does not set SNI.
- At 20:18:04Z, inside the container, `curl -k https://community.tao.internal/` answered 302 to `/portal/`. At 20:18:21Z, from the host, `curl -k --resolve community.tao.internal:3200:127.0.0.1 https://community.tao.internal:3200/` answered 302 as well. The UI was up.
- I stopped `canvas-tao` at 20:18:37Z, in the same command as that probe, 1 min 45 s before the box closed. No login, no import, and no screenshot happened. That is my sequencing error, not a TAO failure.

What the next attempt needs is `docker start canvas-tao` (the sidecars are stopped too, so
`docker compose -f docs/tao-ce.docker-compose.yaml start`), a hosts line
`127.0.0.1 community.tao.internal` or the `--resolve` flag, the default admin login, and then
`POST https://community.tao.internal:3200/taoQtiItem/RestQtiItem/import/` with basic auth and a
QTI 2.1 zip. docs/sat101-q1-qti21.zip is that zip, Q1 of the Canvas export rewritten by hand as a
QTI 2.1 `assessmentItem` with a `choiceInteraction`. The Canvas 1.2 zip is the wrong input for
TAO whatever the route. Whether the 2025 Community Edition still serves the legacy
`/taoQtiItem/` routes, or only the new portal, is the first thing that attempt will learn.

## What the team can commit to

**Custom quiz builder from an external pool into Canvas classic quizzes.** Yes. The classic quiz
and question endpoints work in the sandbox, the parallel workstream has an MCP tool that creates
a quiz from supplied questions, and the R2 agent rule already proposes a 5-question topic quiz
inside a remediation module. The six weeks go to the pool schema, the LTI front end Elijah
proposed for picking topic, count and difficulty, and the MCP tool that turns a pick into a quiz.

**New Quizzes item banks.** Not in six weeks from this sandbox. The banks live in the hosted
quiz-lti service. The sandbox cannot run it, has no "Quizzes 2" tool, and the `quizzes_next`
flag is invisible without Instructure's provisioning setting. It becomes possible only on
Kaplan's hosted Canvas with the flag on and an API route into the service, both of which need
the Canvas admin at Kaplan.

**TAO.** The software is real, free, and has a REST import for QTI 2.1 items and tests. What
blocks a commitment is not the software. It is that Canvas exports QTI 1.2, that nobody has a
TAO instance or credentials on Kaplan's side, and that the Community Edition is a six-container
privileged stack. A laptop runs that stack, the portal answered here, and the import is one
restart away. A TAO deliverable in six weeks is a QTI 2.1 writer from the Redshift pool plus one
verified import, not a delivery integration.

## Recommended design

QBank as a data source means one tagged item pool in Redshift that every consumer reads. The
parallel workstream's `nudges.question_pool` is the seed of it. The full shape:

- One row per item with `item_id`, `topic`, `subtopic`, `difficulty`, `question_type` (one of the twelve classic types), `question_text`, `answers` as JSON in the shape `POST .../questions` accepts, `points_possible`, `provenance` (source bank, author, license), `created_at`, and later `psychometrics` columns such as p-value and discrimination from Canvas quiz statistics.
- Canvas classic quizzes are the delivery surface now. The MCP tool selects rows by topic and difficulty and calls `POST /quizzes` then `POST /quizzes/:id/questions`. Canvas quiz submission data flows back to Redshift through the same nudges pipeline the dashboard reads.
- QTI 2.1 is the interchange format outward. A small writer turns pool rows into a QTI 2.1 package. That package goes to TAO through `POST /taoQtiItem/RestQtiItem/import/` or to Canvas through `qti_converter`, and to New Quizzes through whatever import the hosted service exposes. Writing 2.1 rather than reading Canvas's 1.2 export skips the QTIMigrationTool problem.
- The LTI front end from docs/LTI-EXPLAINED.md is the teacher's builder. It reads the pool, shows counts per topic and difficulty, and calls the MCP tool. The launch code does not change.

What this does not do is replace New Quizzes or TAO for delivery. It feeds them.

## Open questions for Thursday

1. Tealia. What does "similar to QBank" mean in scope? Teacher picks from a pool, or authors new items, or both? Which item types beyond multiple choice?
2. Elijah. Does the LTI front end call the MCP tool directly, or does it write a request row that the nudge agent picks up? Where does the pool writer live?
3. Jithin. Can `nudges.question_pool` grow the columns above, and can Canvas quiz statistics land in Redshift for the psychometrics columns?
4. The Canvas admin at Kaplan. Is `quizzes_next` on for the SAT accounts in production, is there an API token with rights to the quiz-lti service, and does Kaplan hold any TAO instance or contract?
5. Owner TBD. Which QTI version do Kaplan's existing item sources export? If they are 1.2, the pool loader needs the QTIMigrationTool or a hand-written 1.2 reader.
6. Owner TBD. Is a TAO Community Edition instance on a Kaplan host in scope, or is TAO parked until an owner exists?

## Appendix

Canvas calls, all with `-H "Authorization: Bearer cplatform-dev-token"` against
http://localhost:3100.

```
GET  /api/v1/accounts/1/features/enabled?per_page=100
GET  /api/v1/courses/1/features/enabled?per_page=100
GET  /api/v1/accounts/1/external_tools?per_page=100
GET  /api/v1/courses/1/quizzes?per_page=50
GET  /api/v1/courses/1/quizzes/1/questions
GET  /api/v1/question_banks?context_type=Course&context_id=1&include_question_count=true
GET  /api/v1/courses/1/content_migrations/migrators
POST /api/v1/courses/1/content_exports  -d export_type=qti -d "select[quizzes][]=1"   19:46:10Z, 7.45 s, id 1
GET  /api/v1/courses/1/content_exports/1                                               19:46:42Z exporting, 19:48:06Z exported
GET  /files/2/download?download_frd=1  -o docs/sat101-qti-export.zip                   19:48:43Z, 3623 bytes, 7.35 s
```

The zip listing came from Python's zipfile module. Source greps ran from
D:\Canvas\test\canvas\cplatform with `grep -rIl --exclude-dir=node_modules --exclude-dir=spec -e <term> app lib config`.
The migration tool check was `docker exec cplatform-web-1 bundle exec rails runner "puts Qti.migration_executable.inspect"`,
which printed nil.

TAO calls.

```
cd <scratchpad>/tao
docker compose -f docker-compose.tao-ce.yaml config --quiet                    19:50:22Z clock start
docker compose -f docker-compose.tao-ce.yaml pull                              19:50:28Z to 19:55:51Z
docker compose -f docker-compose.tao-ce.yaml up -d                             done 19:56:37Z
until curl -s -k -m 5 -H 'Host: community.tao.internal' https://localhost:3200/ ...   1200 s of 000
docker exec canvas-tao sh -c 'systemctl --failed; systemctl list-units "tao-ce*"; journalctl -n 25'
docker exec canvas-tao curl -s -k -o /dev/null -w '%{http_code}' https://community.tao.internal/     302
curl -s -k --resolve community.tao.internal:3200:127.0.0.1 https://community.tao.internal:3200/       302 at 20:18:21Z
docker stop -t 20 canvas-tao                                                   20:18:37Z, Exited (130)
docker compose -f docker-compose.tao-ce.yaml stop                              sidecars stopped, all six left present
```

Left behind: six stopped containers named `canvas-tao` and `canvas-tao-{es,pgsql,redis,pubsub,firestore}-1`
with label `project=canvas`, five named volumes under the `canvas-tao` compose project, 15.3 GB of
images, docs/sat101-qti-export.zip, docs/sat101-q1-qti21.zip, and docs/tao-ce.docker-compose.yaml.
Remove everything docker with `docker compose -p canvas-tao down -v --rmi all`.

```

```
