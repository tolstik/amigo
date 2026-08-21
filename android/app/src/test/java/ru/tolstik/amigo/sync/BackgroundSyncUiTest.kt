package ru.tolstik.amigo.sync

import androidx.work.ExistingWorkPolicy
import org.junit.Assert.assertEquals
import org.junit.Test
import ru.tolstik.amigo.sync.worker.SyncScheduler

class BackgroundSyncUiTest {
    @Test
    fun backfillAppendsInsteadOfCancellingItsRunningWorker() {
        assertEquals(ExistingWorkPolicy.APPEND_OR_REPLACE, SyncScheduler.backfillPolicy)
    }

    @Test
    fun internalWorkerStatesArePresentedInRussian() {
        assertEquals("Выполняется", backgroundResultLabel("running"))
        assertEquals("Остановлено системой", backgroundResultLabel("cancelled"))
        assertEquals("История загружается частями", backgroundResultLabel("backfill_continues"))
    }
}
