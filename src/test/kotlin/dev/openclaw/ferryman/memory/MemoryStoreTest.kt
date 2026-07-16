package dev.openclaw.ferryman.memory

import java.nio.file.Files
import kotlin.test.AfterTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

class MemoryStoreTest {
    private val tmp = Files.createTempDirectory("ferry-memory-test")
    private val store = MemoryStore(tmp)

    @AfterTest
    fun cleanup() {
        tmp.toFile().deleteRecursively()
    }

    @Test
    fun `save and load round-trips`() {
        store.save("company-research", "EarnIn", "EarnIn uses Compose and is remote-friendly.")

        val loaded = store.load("company-research", "EarnIn")
        assertNotNull(loaded)
        assertEquals("company-research", loaded.category)
        assertEquals("EarnIn", loaded.key)
        assertEquals("EarnIn uses Compose and is remote-friendly.", loaded.content)
        // timestamp is populated and ISO-shaped (has a 'T' separator).
        assertTrue(loaded.timestamp.contains("T"))
    }

    @Test
    fun `save overwrites the same category-key`() {
        store.save("company-research", "EarnIn", "first take")
        store.save("company-research", "EarnIn", "second take")

        val loaded = store.load("company-research", "EarnIn")
        assertNotNull(loaded)
        assertEquals("second take", loaded.content)
        // Only one file for the key.
        assertEquals(1, store.loadAll("company-research").size)
    }

    @Test
    fun `load returns null for missing memory`() {
        assertNull(store.load("company-research", "Nope"))
        assertNull(store.load("missing-category", "anything"))
    }

    @Test
    fun `search finds by keyword in content`() {
        store.save("company-research", "EarnIn", "EarnIn uses Kotlin Multiplatform and Compose.")
        store.save("company-research", "Acme", "Acme is a web-first company.")

        val hits = store.search("compose")
        assertEquals(1, hits.size)
        assertEquals("EarnIn", hits.first().key)
    }

    @Test
    fun `search is case-insensitive`() {
        store.save("company-research", "EarnIn", "Remote-friendly shop in SF.")

        assertEquals(1, store.search("REMOTE").size)
        assertEquals(1, store.search("sf").size)
    }

    @Test
    fun `search matches on key and category too`() {
        store.save("company-research", "EarnIn", "unrelated content")

        assertTrue(store.search("EarnIn").isNotEmpty())
        assertTrue(store.search("company").isNotEmpty())
    }

    @Test
    fun `search with empty query returns nothing`() {
        store.save("company-research", "EarnIn", "content")
        assertTrue(store.search("").isEmpty())
    }

    @Test
    fun `forget deletes the memory and returns true`() {
        store.save("company-research", "EarnIn", "to be deleted")
        assertTrue(store.forget("company-research", "EarnIn"))
        assertNull(store.load("company-research", "EarnIn"))
    }

    @Test
    fun `forget returns false when nothing matched`() {
        assertFalse(store.forget("company-research", "never-existed"))
    }

    @Test
    fun `loadAll returns every memory in a category`() {
        store.save("company-research", "Acme", "one")
        store.save("company-research", "EarnIn", "two")
        store.save("user-preferences", "fit-criteria", "unrelated")

        val research = store.loadAll("company-research")
        assertEquals(2, research.size)
        // sorted by key for stable output
        assertEquals(listOf("Acme", "EarnIn"), research.map { it.key })
    }

    @Test
    fun `loadAll on empty category returns empty list`() {
        assertTrue(store.loadAll("nothing-here").isEmpty())
    }

    @Test
    fun `all returns everything sorted by category then key`() {
        store.save("user-preferences", "zeta", "z")
        store.save("company-research", "EarnIn", "e")
        store.save("company-research", "Acme", "a")

        val all = store.all()
        assertEquals(3, all.size)
        assertEquals("Acme", all[0].key)
        assertEquals("EarnIn", all[1].key)
        assertEquals("zeta", all[2].key)
    }

    @Test
    fun `all returns empty when memory dir does not exist`() {
        val missing = MemoryStore(tmp.resolve("nope"))
        assertTrue(missing.all().isEmpty())
        assertNull(missing.load("any", "thing"))
    }

    @Test
    fun `ensureSeeded writes the fit-criteria memory once`() {
        val freshDir = tmp.resolve("fresh")
        val fresh = MemoryStore(freshDir)
        assertFalse(Files.isDirectory(freshDir))

        fresh.ensureSeeded()

        val seeded = fresh.load(MemoryStore.USER_PREFERENCES, MemoryStore.FIT_CRITERIA)
        assertNotNull(seeded)
        assertEquals(MemoryStore.USER_PREFERENCES, seeded.category)
        assertEquals(MemoryStore.FIT_CRITERIA, seeded.key)
        assertEquals(MemoryStore.SEEDED_AT, seeded.timestamp)
        assertTrue(seeded.content.contains("Jetpack Compose"))
        assertTrue(seeded.content.contains("KMP"))
        assertTrue(seeded.content.contains("remote-friendly"))
        assertTrue(seeded.content.contains("SF Bay Area"))
        assertTrue(seeded.content.contains("AI-native"))
        assertTrue(seeded.content.contains("mobile-first"))
    }

    @Test
    fun `ensureSeeded is idempotent and does not clobber existing memories`() {
        val freshDir = tmp.resolve("fresh2")
        val fresh = MemoryStore(freshDir)
        // First seed creates the dir + memory.
        fresh.ensureSeeded()
        // Manually add a memory so we can confirm a second seed leaves it alone.
        fresh.save("company-research", "Acme", "acme research")
        // Create the dir but then mutate the seeded content to detect overwrite.
        fresh.save(MemoryStore.USER_PREFERENCES, MemoryStore.FIT_CRITERIA, "overridden by user")

        fresh.ensureSeeded() // should be a no-op now that the dir exists

        assertEquals("overridden by user", fresh.load(MemoryStore.USER_PREFERENCES, MemoryStore.FIT_CRITERIA)?.content)
        assertNotNull(fresh.load("company-research", "Acme"))
    }

    @Test
    fun `file names are sanitized so keys cannot escape the memory dir`() {
        // A key with slashes / dots that would otherwise traverse directories.
        store.save("company-research", "../sneaky", "content")

        // Lookup still works after sanitization.
        assertNotNull(store.load("company-research", "../sneaky"))
        // No file escaped the root (no file named 'sneaky' above the dir).
        assertFalse(Files.exists(tmp.parent.resolve("sneaky")))
    }

    @Test
    fun `toJson serialises a list as a JSON array`() {
        store.save("company-research", "EarnIn", "uses Compose")

        val json = memoriesToJson(store.all())
        assertTrue(json.startsWith("["))
        assertTrue(json.endsWith("]"))
        assertTrue(json.contains("\"category\":\"company-research\""))
        assertTrue(json.contains("\"key\":\"EarnIn\""))
        assertTrue(json.contains("\"content\":\"uses Compose\""))
    }

    @Test
    fun `corrupt memory file does not crash all() or search`() {
        store.save("company-research", "EarnIn", "good content")
        // Drop a non-JSON file in the dir — must be skipped, not fatal.
        Files.writeString(tmp.resolve("not-a-memory.json"), "{ this is not json")
        // And a valid file with the wrong shape.
        Files.writeString(tmp.resolve("wrong-shape.json"), "123")

        val all = store.all()
        assertEquals(1, all.size)
        assertEquals("EarnIn", all.first().key)
        assertTrue(store.search("good").isNotEmpty())
    }
}
