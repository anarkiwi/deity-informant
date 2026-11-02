# External validation tools for layout recovery: Cadaver's SIDId, Linus Akesson's sidreloc and
# GoatTracker 2's gt2reloc (with a JSON listing of its assembler's atoms and symbols).
# Each tool is fetched, verified and built in its own cached stage; the sources are never
# committed. Build: docker build -f docker/vtools.Dockerfile -t di-vtools docker/vtools

ARG SIDID_COMMIT=9f0cb6895a84232c59b1fa761424f4a42115e855
ARG SIDRELOC_URL=https://hd0.linusakesson.net/files/sidreloc-1.0.tgz
ARG SIDRELOC_SHA256=8ca55fb4886bda2a499f837e2f9ffd0a4b7217ee7bb1907ceed9e87ef6157bf6
ARG GT2_URL=https://sourceforge.net/projects/goattracker2/files/GoatTracker%202/2.77/GoatTracker_2.77.zip/download
ARG GT2_SHA256=96c2bd6a6ab3aca2f5bb18b1c764ac6ea69ac245cae14002a72cd87c554561ef

FROM debian:trixie-slim AS toolchain
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential ca-certificates curl git unzip \
       libsdl1.2-compat-dev \
    && rm -rf /var/lib/apt/lists/*

FROM toolchain AS sidid
ARG SIDID_COMMIT
WORKDIR /src
RUN git init -q . && git fetch -q --depth 1 https://github.com/cadaver/sidid "$SIDID_COMMIT" \
    && git checkout -q FETCH_HEAD && test "$(git rev-parse HEAD)" = "$SIDID_COMMIT" \
    && gcc -Wall -O2 -o sidid sidid.c

FROM toolchain AS sidreloc
ARG SIDRELOC_URL
ARG SIDRELOC_SHA256
WORKDIR /src
RUN curl -fsSL -o s.tgz "$SIDRELOC_URL" && echo "$SIDRELOC_SHA256  s.tgz" | sha256sum -c - \
    && tar xzf s.tgz --strip-components=1 && make

FROM toolchain AS gt2src
ARG GT2_URL
ARG GT2_SHA256
WORKDIR /gt2
RUN curl -fsSL -o gt2.zip "$GT2_URL" && echo "$GT2_SHA256  gt2.zip" | sha256sum -c - \
    && unzip -q gt2.zip && rm gt2.zip

FROM gt2src AS gt2
COPY gt2-listing.patch /tmp/
WORKDIR /gt2/src
RUN sed -i 's/\r$//' greloc.c asm/parse.c asm/parse.h && patch -d .. -p1 < /tmp/gt2-listing.patch \
    && find . -name '*.o' -delete && rm -f bme/*.exe goatdata.c goattrk2.dat \
    && make -C bme datafile dat2inc \
    && make -f makefile ../linux/gt2reloc

FROM debian:trixie-slim AS run
RUN apt-get update \
    && apt-get install -y --no-install-recommends libsdl1.2debian \
    && rm -rf /var/lib/apt/lists/*
COPY --from=sidid /src/sidid /usr/local/bin/
COPY --from=sidid /src/sidid.cfg /src/tedid.cfg /src/sidid.nfo /usr/local/share/sidid/
COPY --from=sidreloc /src/sidreloc /usr/local/bin/
COPY --from=gt2 /gt2/linux/gt2reloc /usr/local/bin/
COPY --from=gt2src /gt2/examples /usr/local/share/gt2/examples
COPY sidid-list /usr/local/bin/
ENV SIDIDCFG=/usr/local/share/sidid/sidid.cfg
