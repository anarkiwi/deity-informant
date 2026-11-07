# MLIR tools (apt.llvm.org) for tools/structure.py: mlir-opt and mlir-translate.
# The install layer is isolated so source edits do not rebuild it.
ARG LLVM_VERSION=22

FROM ubuntu:26.04 AS mlir
ARG LLVM_VERSION
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
    && curl -fsSL https://apt.llvm.org/llvm-snapshot.gpg.key | gpg --dearmor -o /usr/share/keyrings/llvm.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/llvm.gpg] https://apt.llvm.org/noble/ llvm-toolchain-noble-${LLVM_VERSION} main" > /etc/apt/sources.list.d/llvm.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends mlir-${LLVM_VERSION}-tools \
    && ln -s /usr/lib/llvm-${LLVM_VERSION}/bin/mlir-opt /usr/local/bin/mlir-opt \
    && ln -s /usr/lib/llvm-${LLVM_VERSION}/bin/mlir-translate /usr/local/bin/mlir-translate \
    && rm -rf /var/lib/apt/lists/*

FROM mlir AS run
WORKDIR /work
