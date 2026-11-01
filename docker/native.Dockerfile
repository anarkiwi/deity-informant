ARG PYTHON_VERSION=3.12

FROM rust:1.99-slim-trixie AS rust

FROM python:${PYTHON_VERSION}-slim-trixie AS pydeps
WORKDIR /src
COPY pyproject.toml .
# jennings depends on deity-informant, so pip pulls a released one that is then removed.
RUN python -c "import tomllib; p = tomllib.load(open('pyproject.toml', 'rb'))['project']; \
print('\n'.join(p['dependencies'] + p['optional-dependencies']['dev']))" > /requirements.txt \
    && pip install --no-cache-dir -r /requirements.txt \
    && pip uninstall -y deity-informant

FROM python:${PYTHON_VERSION}-slim-trixie AS toolchain
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*
ENV RUSTUP_HOME=/usr/local/rustup CARGO_HOME=/usr/local/cargo PATH=/usr/local/cargo/bin:$PATH
COPY --from=rust /usr/local/rustup /usr/local/rustup
COPY --from=rust /usr/local/cargo /usr/local/cargo
RUN pip install --no-cache-dir maturin

FROM toolchain AS build
WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY crates/di-pcode/Cargo.toml crates/di-pcode/
COPY crates/di-py/Cargo.toml crates/di-py/
RUN mkdir -p crates/di-pcode/src crates/di-py/src \
    && touch crates/di-pcode/src/lib.rs crates/di-py/src/lib.rs \
    && cargo fetch --locked
COPY pyproject.toml README.md LICENSE ./
COPY crates crates
COPY deity_informant deity_informant
RUN maturin build --release --locked -i python -o /wheels

FROM pydeps AS test
ENV OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir --no-deps /wheels/*.whl \
    && python -c "import deity_informant as d; assert d.NATIVE" \
    && deity-informant emit-sleigh
# Only tests, tools and docs are copied so imports resolve to the installed wheel.
COPY README.md ./
COPY docs docs
COPY tests tests
COPY tools tools
COPY crates/di-pcode/src/templates_gen.rs crates/di-pcode/src/templates_gen.rs
CMD ["sh", "-c", "pytest tests -n auto -q && python tools/gen_pcode.py --check"]
