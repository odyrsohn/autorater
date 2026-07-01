FROM golang:1.22-alpine AS build
WORKDIR /src
COPY go.mod ./
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/alerting .

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/alerting /alerting
EXPOSE 8070
ENTRYPOINT ["/alerting"]
