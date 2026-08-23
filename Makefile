.PHONY: dev test test-e2e

dev:
	pnpm dev

test:
	pnpm test

test-e2e:
	pnpm test:e2e
