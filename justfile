DC := "docker-compose"
RUN := DC + " run --rm web"


build:
	{{ DC }} build

up service="":
	{{ DC }} up -d {{ service }}

down:
	{{ DC }} down

fresh-start:
	# Tear down existing containers, remove volume
	{{ DC }} down
	docker volume rm openoversight_postgres openoversight_minio || true
	{{ DC }} build

	# Start up and populate fields
	{{ RUN }} python ../create_db.py
	{{ RUN }} flask make-admin-user
	{{ RUN }} flask add-department "Seattle Police Department" "SPD"
	{{ RUN }} flask bulk-add-officers /data/init_data.csv
