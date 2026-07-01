package dedupe

import (
	"sync"
	"testing"
	"time"
)

func newTestCache(ttl time.Duration) (*Cache, *time.Time) {
	now := time.Unix(0, 0)
	c := New(ttl)
	c.now = func() time.Time { return now }
	return c, &now
}

func TestDuplicateSuppressedWithinTTL(t *testing.T) {
	c, _ := newTestCache(time.Minute)

	if ok, _ := c.Admit("fp1"); !ok {
		t.Fatal("first alert must be admitted")
	}
	ok, dups := c.Admit("fp1")
	if ok || dups != 1 {
		t.Fatalf("duplicate must be suppressed and counted, got ok=%v dups=%d", ok, dups)
	}
}

func TestExpiryReadmits(t *testing.T) {
	c, now := newTestCache(time.Minute)

	c.Admit("fp1")
	*now = now.Add(2 * time.Minute)
	if ok, _ := c.Admit("fp1"); !ok {
		t.Fatal("expired fingerprint must be re-admitted")
	}
}

func TestDistinctFingerprintsIndependent(t *testing.T) {
	c, _ := newTestCache(time.Minute)

	c.Admit("fp1")
	if ok, _ := c.Admit("fp2"); !ok {
		t.Fatal("different fingerprint must not be suppressed")
	}
}

func TestGCEvictsExpired(t *testing.T) {
	c, now := newTestCache(time.Minute)

	c.Admit("old1")
	c.Admit("old2")
	*now = now.Add(2 * time.Minute)
	c.Admit("fresh")
	if c.Size() != 1 {
		t.Fatalf("expired entries must be GCed, size=%d", c.Size())
	}
}

func TestConcurrentAdmitSingleWinner(t *testing.T) {
	c := New(time.Minute)
	var wg sync.WaitGroup
	admitted := make(chan bool, 100)
	for i := 0; i < 100; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			ok, _ := c.Admit("same")
			admitted <- ok
		}()
	}
	wg.Wait()
	close(admitted)

	wins := 0
	for ok := range admitted {
		if ok {
			wins++
		}
	}
	if wins != 1 {
		t.Fatalf("exactly one concurrent admit must win, got %d", wins)
	}
}
