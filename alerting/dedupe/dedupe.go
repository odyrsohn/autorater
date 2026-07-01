// Package dedupe suppresses repeated alerts for the same regression
// fingerprint within a TTL window, so one bad deploy pages once, not
// once per mined failure case.
package dedupe

import (
	"sync"
	"time"
)

// Cache is a concurrency-safe TTL set keyed by alert fingerprint.
type Cache struct {
	mu      sync.Mutex
	entries map[string]entry
	ttl     time.Duration
	now     func() time.Time
}

type entry struct {
	expires time.Time
	count   int
}

func New(ttl time.Duration) *Cache {
	return &Cache{entries: make(map[string]entry), ttl: ttl, now: time.Now}
}

// Admit returns true when the fingerprint has not been seen within the TTL;
// duplicates refresh nothing but are counted for the suppression metric.
func (c *Cache) Admit(fingerprint string) (admitted bool, duplicates int) {
	c.mu.Lock()
	defer c.mu.Unlock()

	now := c.now()
	if e, ok := c.entries[fingerprint]; ok && now.Before(e.expires) {
		e.count++
		c.entries[fingerprint] = e
		return false, e.count
	}
	c.entries[fingerprint] = entry{expires: now.Add(c.ttl)}
	c.gcLocked(now)
	return true, 0
}

// gcLocked drops expired fingerprints; called opportunistically on writes.
func (c *Cache) gcLocked(now time.Time) {
	for k, e := range c.entries {
		if now.After(e.expires) {
			delete(c.entries, k)
		}
	}
}

// Size reports live fingerprints (for metrics/tests).
func (c *Cache) Size() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return len(c.entries)
}
