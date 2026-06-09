# Multi-stage build for the StarHarbour Payments Service (the "Central System").
#
# The upstream repo (vendored as the ./external submodule) only containerises
# Toxiproxy and expects you to run the app via `./gradlew bootRun` with a local
# Java 25 toolchain. Here we containerise the Spring Boot app itself so the whole
# stack comes up with a single `docker compose up` — no local Java needed.
#
# Build context is the repo root (see docker-compose.yml + .dockerignore), so we
# copy from the external/ submodule path.

# ---- build stage ------------------------------------------------------------
FROM eclipse-temurin:25-jdk AS build
WORKDIR /build

# Copy the Gradle wrapper + build scripts first so dependency resolution is cached
# independently of source changes.
COPY external/gradlew external/settings.gradle.kts external/build.gradle.kts ./
COPY external/gradle ./gradle
RUN chmod +x ./gradlew && ./gradlew --no-daemon dependencies > /dev/null 2>&1 || true

# Now the sources, then build the runnable fat jar (skip tests — built upstream).
COPY external/src ./src
RUN ./gradlew --no-daemon clean bootJar -x test

# ---- runtime stage ----------------------------------------------------------
FROM eclipse-temurin:25-jre AS runtime
WORKDIR /app

# curl is used by the Compose healthcheck (and handy for debugging inside the container).
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# The bootJar is the only *-SNAPSHOT.jar produced (plain jar is disabled by default).
COPY --from=build /build/build/libs/*-SNAPSHOT.jar app.jar

# The production jar does not include spring-boot-docker-compose (developmentOnly),
# so it won't try to launch Compose from inside the container. Set the flag anyway
# as belt-and-suspenders.
ENV SPRING_DOCKER_COMPOSE_ENABLED=false

EXPOSE 8080
ENTRYPOINT ["java", "-jar", "app.jar"]
