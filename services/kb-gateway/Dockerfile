# kb-gateway — static Go binary, distroless.
FROM golang:1.24-alpine AS build
WORKDIR /src
COPY services/kb-gateway/go.mod ./
RUN go mod download
COPY services/kb-gateway/ ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /kb-gateway .

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /kb-gateway /kb-gateway
EXPOSE 8000
USER nonroot:nonroot
ENTRYPOINT ["/kb-gateway"]
