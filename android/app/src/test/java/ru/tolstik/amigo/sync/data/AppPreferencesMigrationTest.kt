package ru.tolstik.amigo.sync.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import ru.tolstik.amigo.sync.sync.RecordType

class AppPreferencesMigrationTest {
    @Test
    fun versionTwoReconcilesOnlyHeartRateForAnExistingRegistration() {
        val migration = heartRateReconciliationMigration(
            currentVersion = 1,
            hasRegistration = true,
        )

        assertEquals(2, migration?.targetVersion)
        assertEquals(setOf(RecordType.HEART_RATE), migration?.recordTypes)
        assertEquals(
            setOf("changes_token", "cursor", "target"),
            migration?.removedStateSuffixes,
        )
        assertTrue(
            RecordType.entries
                .filterNot { it == RecordType.HEART_RATE }
                .none { it in migration!!.recordTypes },
        )
    }

    @Test
    fun versionTwoIsAppliedExactlyOnce() {
        assertNull(
            heartRateReconciliationMigration(
                currentVersion = 2,
                hasRegistration = true,
            )
        )
    }

    @Test
    fun freshPairingKeepsTheInitialSnapshotStateUntouched() {
        val migration = heartRateReconciliationMigration(
            currentVersion = 0,
            hasRegistration = false,
        )

        assertEquals(2, migration?.targetVersion)
        assertTrue(migration?.recordTypes.orEmpty().isEmpty())
    }
}
