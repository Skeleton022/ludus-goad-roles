FROM php:8.4.26-apache
# The Compose version bind-mounted this static landing page at runtime.
COPY --chown=www-data:www-data index.php /var/www/html/index.php
# Lab flag for the Phase D exercise (contents come from ./flag.txt).
COPY flag.txt /flag.txt
RUN chmod 0777 /flag.txt
EXPOSE 80
