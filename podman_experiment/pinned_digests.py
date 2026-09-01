# pinned_digests.py — read-only copy of the image digests pinned in
# docker-compose.yml (repo root), for the Podman checkpoint/restore
# experiment. docker-compose.yml itself is NOT modified.
#
# Keeping these in sync manually (not importing docker-compose.yml) avoids
# any dependency of this experimental code on the production compose file.
# If docker-compose.yml's digests ever change, update this file by hand.

POSTGRES_IMAGE = "docker.io/library/postgres@sha256:52e6ffd11fddd081ae63880b635b2a61c14008c17fc98cdc7ce5472265516dd0"
REDIS_IMAGE    = "docker.io/library/redis@sha256:1f073813b641755b70b0200da64131bbeeb4ec5b633ca67772229b49820caafa"
MYSQL_IMAGE    = "docker.io/library/mysql@sha256:24e450bbd24f621c71b10404c946cc9ea1cbb0e6e7464b2be2de5193dcf1d05b"

# Throwaway container names — distinct from docker-compose.yml's
# postgres/redis/mysql, and from any container Docker itself would create.
# No host ports are published and no named volumes are attached: these
# containers use ephemeral, container-local storage only.
CONTAINERS = {
    "postgres": {
        "image": POSTGRES_IMAGE,
        "name":  "pg-podman-ckpt-exp",
        "env":   ["POSTGRES_PASSWORD=exp_pass", "POSTGRES_DB=expdb"],
    },
    "redis": {
        "image": REDIS_IMAGE,
        "name":  "redis-podman-ckpt-exp",
        "env":   [],
    },
    "mysql": {
        "image": MYSQL_IMAGE,
        "name":  "mysql-podman-ckpt-exp",
        "env":   ["MYSQL_ROOT_PASSWORD=exp_pass", "MYSQL_DATABASE=expdb"],
    },
}
