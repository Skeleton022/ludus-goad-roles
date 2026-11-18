# php2shell

Minimal Ansible role for the PHP2Shell training container. It uses the four supplied trainer files verbatim. Only the host-side Docker build/run orchestration is implemented with Ansible. It does **not** change the Dockerfile, Apache configuration, PHP configuration, index.php or flag.txt.

- Base image: `php:8.4.26-apache` from the supplied Dockerfile.
- Host path for all four original files: `/opt/php2shell`.
- Host TCP/80 -> container TCP/80 by default, bind `0.0.0.0`.
- Container/image: `php2shell` / `php2shell:8.4.26`.
- No Docker Compose, no custom `php.ini`, no privileged container, no mounted host volumes.
- Docker Engine must already be installed (for Ludus, place `geerlingguy.docker` before this role).
- Builds only on input changes or if the image is missing; recreates the container only if needed.
- Avoids stopping unrelated containers that publish the same host port.

## Original `build_docker.sh` caveat

The supplied script ends in `docker run -p 1337:80 -it --rm --name=aaaaa` and omits the required IMAGE argument. It is preserved byte-for-byte in `files/build_docker.sh` and copied to the build directory for provenance, but **not executed**. The Ansible task uses `docker run -d ... -p 0.0.0.0:80:80 php2shell:8.4.26` for unattended deployment.

## Configuration defaults

```yaml
php2shell_root: /opt/php2shell
php2shell_http_port: 80
php2shell_bind_address: "0.0.0.0"
php2shell_image: "php2shell:8.4.26"
php2shell_container_name: "php2shell"
```

Do not change the image tag if you want to keep the same trainer PHP build.

## Ludus import

On the Ludus host, from the extracted archive root:

```bash
ludus ansible role add -d ./ansible/roles/php2shell --global
```

If replacing a previously imported version of the same role:

```bash
ludus ansible role add -d ./ansible/roles/php2shell --global --force
```

## Student3 range

In the `STUDENT3-PHP2SHELL` VM's `roles:` array, **replace** `php2shell_onepager` with `php2shell`, keeping Docker first and Elastic Agent last:

```yaml
roles:
  - geerlingguy.docker
  - php2shell
  - name: badsectorlabs.ludus_elastic_agent
    depends_on:
      - vm_name: "{{ range_id }}-elastic"
        role: badsectorlabs.ludus_elastic_container
role_vars:
  docker_edition: ce
  docker_install_compose_plugin: true
  ludus_elastic_agent_version: "9.5.5"
  ludus_elastic_install_sysmon: false
  php2shell_http_port: 80
  php2shell_bind_address: "0.0.0.0"
```

Then apply the range config and deploy only the new role on the existing VM:

```bash
ludus -u student3 range config set -f scenario2_config.yml
ludus -u student3 range deploy -t user-defined-roles --limit STUDENT3-PHP2SHELL --only-roles php2shell
```

**Potential host TCP/80 conflict:** If the old `php2shell_onepager` Docker container still publishes host TCP/80, this role **stops with a readable error** rather than removing another container without permission. Check `docker ps` on STUDENT3-PHP2SHELL, stop the previous challenge container manually, and rerun only this role. Removing the previous role from the YAML prevents future role deployments from redeploying it.

## Checks

On `STUDENT3-PHP2SHELL`:

```bash
docker ps
docker exec php2shell php -v
docker exec php2shell ls -l /flag.txt
curl -I http://127.0.0.1/
```

On a WireGuard-connected student client: `http://10.6.20.50/`.

The role checks PHP version, flag file presence and an HTTP 200 response; it does not assert that a particular exploit succeeds. A successful exploit must be confirmed separately in the authorized training range.
