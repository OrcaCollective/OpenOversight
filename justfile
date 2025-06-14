IS_PROD := env_var_or_default("IS_PROD", "")
COMPOSE_FILE := "--file=docker-compose.yml" + (
    if IS_PROD == "true" {" --file=docker-compose.prod.yml"}
    else {" --file=docker-compose.dev.yml"}
)
DC := "docker compose " + COMPOSE_FILE
RUN := DC + " run --rm"
RUN_WEB := RUN + " web"
FOLLOW_LOGS := env("FOLLOW_LOGS", "-f")
set dotenv-load := false
# Force just to hand down positional arguments so quoted arguments with spaces are
# handled appropriately
set positional-arguments


default:
    @just -ul

# Create the .env file from the template
dotenv:
    @([ ! -f .env ] && cp .env.example .env) || true

# Create an empty service_account_key.json file
service-account-key:
    @([ ! -f service_account_key.json ] && touch service_account_key.json) || true


# Install dev dependencies using poetry
install:
    poetry install

# Build all containers
build: dotenv service-account-key
	{{ DC }} build

# Spin up all (or the specified) services
up *args:
	{{ DC }} up -d {{ args }}

# Tear down all services
down *args:
	{{ DC }} down {{ args }}

# Attach logs to all (or the specified) services
logs *args:
	{{ DC }} logs {{ FOLLOW_LOGS }} {{ args }}

# Pull all docker images
pull:
    {{ DC }} pull

# Pull, migrate, and deploy all images
deploy: && pull (db "upgrade") up
    -git pull

# Tear down the database, remove the volumes, recreate the database, and populate it with sample data
fresh-start: dotenv
	# Tear down existing containers, remove volume
	@just down -v
	@just build

	# Prepare the database
	{{ RUN_WEB }} python create_db.py
	{{ RUN_WEB }} flask db stamp

	# Populate users and data
	{{ RUN_WEB }} flask make-admin-user --username admin --email admin@admin.com --password admin
	{{ RUN_WEB }} flask add-department "Seattle Police Department" "SPD" "WA"
	{{ RUN_WEB }} flask bulk-add-officers -y /data/init_data.csv

	# Start containers
	@just up

# Run a command on a provided service
run *args:
	{{ RUN }} "$@"

# Launch into a database shell
db-shell:
    {{ DC }} exec postgres psql -U openoversight openoversight

# Import a CSV file
import +args:
	{{ RUN_WEB }} flask advanced-csv-import {{ args }}

# Run the static checks
lint:
    pre-commit run --all-files

# Generate poetry lockfile
lock:
    just run --no-deps web poetry lock

# Run Flask-Migrate tasks in the web container
db +migrateargs:
    just run web flask db {{ migrateargs }}

# Run unit tests in the web container
test *pytestargs:
    just run --no-deps web pytest -n auto {{ pytestargs }}

# Back up the postgres data using loomchild/volume-backup
backup location:
    docker run --rm \
        -v openoversight_postgres:/volume \
        -v {{ location }}:/backup \
        loomchild/volume-backup \
        backup openoversight-postgres-$(date '+%Y-%m-%d').tar.bz2

# Build the docs using sphinx
make-docs:
    poetry run sphinx-build -b html docs/ docs/_build/html

# Build & serve the docs using a live server
serve-docs:
    poetry run sphinx-autobuild docs/ docs/_build/html
