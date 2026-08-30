# kb-compute — static Go binary, distroless. ~15 MB image, ~5 MB RSS.
FROM golang:1.24-alpine AS build
WORKDIR /src
COPY backend/services/kb-compute/go.mod ./
# (no external deps yet — stdlib only; `go mod download` is a no-op but keeps the
#  layer cache-friendly once deps are added)
RUN go mod download
COPY backend/services/kb-compute/ ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /kb-compute .

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /kb-compute /kb-compute
EXPOSE 8600
USER nonroot:nonroot
ENTRYPOINT ["/kb-compute"]
