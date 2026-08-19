package ru.tolstik.amigo.sync

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.lightColorScheme
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

class PermissionsRationaleActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme(colorScheme = lightColorScheme()) {
                Column(
                    modifier = Modifier.fillMaxSize().padding(24.dp),
                    verticalArrangement = Arrangement.spacedBy(16.dp),
                ) {
                    Text("Как Amigo использует данные", style = MaterialTheme.typography.headlineSmall)
                    Text(
                        "Amigo Sync читает выбранные вами показатели активности, сна и пульса " +
                            "из Health Connect и отправляет их только на ваш сервер Amigo по HTTPS.",
                    )
                    Text(
                        "Приложение не запрашивает запись данных, вес, давление, геопозицию и маршруты. " +
                            "Разрешения можно отозвать в Health Connect в любое время.",
                    )
                }
            }
        }
    }
}
