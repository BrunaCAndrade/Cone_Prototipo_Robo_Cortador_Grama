/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Main program body
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2025 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"
#include "cmsis_os.h"
#include "dma.h"
#include "tim.h"
#include "usart.h"
#include "gpio.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include "FreeRTOS.h"
#include <stdlib.h>   // atoi
#include "task.h"
#include "queue.h"
#include "semphr.h"
#include <string.h>
#include <stdio.h>
#include <ctype.h>

/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */
#define RX_QUEUE_LEN        128
#define CMD_LINE_MAX        64
#define UART_TX_TIMEOUT_MS  1000
#define MOTOR_PWM_CCR_FIXED 4000U

/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/

/* USER CODE BEGIN PV */
// UART2 RX (1 byte por interrupção)
static uint8_t uart2_rx_byte = 0;
static QueueHandle_t q_uart2_rx = NULL;

// UART2 TX sync
static SemaphoreHandle_t sem_uart2_tx = NULL;
static SemaphoreHandle_t mtx_uart2_tx = NULL;

// Console
static char cmd_line[CMD_LINE_MAX];
static uint32_t cmd_len = 0;

// Estado do motor
static uint8_t motor_enabled = 0;

/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
void MX_FREERTOS_Init(void);
/* USER CODE BEGIN PFP */

/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */
static void uart2_write_blocking_it(const uint8_t *data, uint16_t len, TickType_t timeoutTicks)
{
    if (data == NULL || len == 0) return;
    if (mtx_uart2_tx == NULL || sem_uart2_tx == NULL) return;

    if (xSemaphoreTake(mtx_uart2_tx, timeoutTicks) != pdTRUE) return;

    // garante semáforo "vazio" antes de iniciar TX
    (void)xSemaphoreTake(sem_uart2_tx, 0);

    if (HAL_UART_Transmit_IT(&huart2, (uint8_t*)data, len) == HAL_OK)
    {
        (void)xSemaphoreTake(sem_uart2_tx, timeoutTicks);
    }

    xSemaphoreGive(mtx_uart2_tx);
}

static void uart2_write_str(const char *s)
{
    uart2_write_blocking_it((const uint8_t*)s, (uint16_t)strlen(s), pdMS_TO_TICKS(UART_TX_TIMEOUT_MS));
}

void HAL_UART_TxCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance != USART2) return;

    BaseType_t hpw = pdFALSE;
    xSemaphoreGiveFromISR(sem_uart2_tx, &hpw);
    portYIELD_FROM_ISR(hpw);
}

void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance != USART2) return;

    BaseType_t hpw = pdFALSE;
    if (q_uart2_rx != NULL)
    {
        xQueueSendFromISR(q_uart2_rx, &uart2_rx_byte, &hpw);
    }

    // rearma recepção de 1 byte
    (void)HAL_UART_Receive_IT(&huart2, &uart2_rx_byte, 1);
    portYIELD_FROM_ISR(hpw);
}

void HAL_UART_ErrorCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance != USART2) return;
    // rearma RX em caso de erro
    (void)HAL_UART_Receive_IT(&huart2, &uart2_rx_byte, 1);
}

// Motores
static void motors_safe_off_pins(void)
{
    HAL_GPIO_WritePin(GPIOB, OUT_1_Pin, GPIO_PIN_RESET);
    HAL_GPIO_WritePin(GPIOB, OUT_2_Pin, GPIO_PIN_RESET);
    HAL_GPIO_WritePin(GPIOB, OUT_3_Pin, GPIO_PIN_RESET);
    HAL_GPIO_WritePin(GPIOB, OUT_4_Pin, GPIO_PIN_RESET);
}

static void motors_forward_pins(void)
{
    HAL_GPIO_WritePin(GPIOB, OUT_3_Pin, GPIO_PIN_RESET);
    HAL_GPIO_WritePin(GPIOB, OUT_4_Pin, GPIO_PIN_SET);
    HAL_GPIO_WritePin(GPIOB, OUT_1_Pin, GPIO_PIN_RESET);
    HAL_GPIO_WritePin(GPIOB, OUT_2_Pin, GPIO_PIN_SET);
}

//funcao para testar
static void motors_set_pwm_ccr(uint32_t ccr)
{
    uint32_t arr = __HAL_TIM_GET_AUTORELOAD(&htim3);

    // Garante CCR válido
    if (ccr > arr) ccr = arr;

    __HAL_TIM_SET_COMPARE(&htim3, TIM_CHANNEL_1, ccr);
    __HAL_TIM_SET_COMPARE(&htim3, TIM_CHANNEL_2, ccr);
}


static void motors_off(void)
{
    motors_set_pwm_ccr(0);
    motors_safe_off_pins();
    motor_enabled = 0;
}

static void motors_on(void)
{
    motors_forward_pins();
    motors_set_pwm_ccr(MOTOR_PWM_CCR_FIXED);
    motor_enabled = 1;
}

// console
static void print_help(void)
{
    uart2_write_str(
        "\r\nCommands:\r\n"
        "  help     - show this help\r\n"
        "  clear    - clear terminal\r\n"
        "  on       - motors ON (fixed PWM)\r\n"
        "  off      - motors OFF\r\n"
        "  status   - show motor status\r\n"
#if (configUSE_TRACE_FACILITY == 1) && (configUSE_STATS_FORMATTING_FUNCTIONS == 1)
        "  tasks    - list tasks\r\n"
#endif
#if (configGENERATE_RUN_TIME_STATS == 1)
        "  runtime  - runtime stats\r\n"
#endif
        "\r\n"
    );
}

static void handle_line(const char *line)
{
    // normaliza: remove espaços e lower->upper
    char cmd[CMD_LINE_MAX];
    size_t k = 0;
    for (size_t i = 0; line[i] && k < sizeof(cmd)-1; i++)
    {
        if (isspace((unsigned char)line[i])) continue;
        cmd[k++] = (char)toupper((unsigned char)line[i]);
    }
    cmd[k] = 0;

    if (strcmp(cmd, "HELP") == 0) { print_help(); return; }
    if (strcmp(cmd, "CLEAR") == 0) { uart2_write_str("\033c"); return; }

    if (strcmp(cmd, "ON") == 0) { motors_on(); uart2_write_str("OK ON\r\n"); return; }
    if (strcmp(cmd, "OFF") == 0) { motors_off(); uart2_write_str("OK OFF\r\n"); return; }
    if (strcmp(cmd, "STATUS") == 0)
    {
        char b[160];

        uint32_t arr  = __HAL_TIM_GET_AUTORELOAD(&htim3);
        uint32_t ccr1 = __HAL_TIM_GET_COMPARE(&htim3, TIM_CHANNEL_1);
        uint32_t ccr2 = __HAL_TIM_GET_COMPARE(&htim3, TIM_CHANNEL_2);

        snprintf(b, sizeof(b),
                 "EN=%u | FIXED_CCR=%lu | ARR=%lu CCR1=%lu CCR2=%lu\r\n",
                 motor_enabled,
                 (unsigned long)MOTOR_PWM_CCR_FIXED,
                 (unsigned long)arr,
                 (unsigned long)ccr1,
                 (unsigned long)ccr2);

        uart2_write_str(b);
        return;
    }

#if (configUSE_TRACE_FACILITY == 1) && (configUSE_STATS_FORMATTING_FUNCTIONS == 1)
    if (strcmp(cmd, "TASKS") == 0)
    {
        char out[512];
        strcpy(out, "Name\t\tState Prio Stack Num\r\n");
        vTaskList(out + strlen(out));
        uart2_write_str(out);
        return;
    }
#endif

#if (configGENERATE_RUN_TIME_STATS == 1)
    if (strcmp(cmd, "RUNTIME") == 0)
    {
        char out[512];
        strcpy(out, "Name\t\tAbs Time\t% Time\r\n");
        vTaskGetRunTimeStats(out + strlen(out));
        uart2_write_str(out);
        return;
    }
#endif

    uart2_write_str("ERR (type help)\r\n");
}

// tasks
static void shell_task(void *arg)
{
    (void)arg;

    uart2_write_str("\033cF411 UART2 console ready\r\n");
    print_help();
    uart2_write_str(">> ");

    for (;;)
    {
        uint8_t c;
        if (xQueueReceive(q_uart2_rx, &c, portMAX_DELAY) != pdTRUE)
            continue;

        // eco (opcional)
        uart2_write_blocking_it(&c, 1, pdMS_TO_TICKS(100));

        if (c == '\r' || c == '\n')
        {
            uart2_write_str("\r\n");
            if (cmd_len > 0)
            {
                cmd_line[cmd_len] = 0;
                handle_line(cmd_line);
                cmd_len = 0;
                memset(cmd_line, 0, sizeof(cmd_line));
            }
            uart2_write_str(">> ");
        }
        else if (c == 0x7F || c == 0x08) // backspace
        {
            if (cmd_len > 0)
            {
                cmd_len--;
                uart2_write_str("\b \b");
            }
        }
        else if (cmd_len < (CMD_LINE_MAX - 1))
        {
            cmd_line[cmd_len++] = (char)c;
        }
    }
}

static void blink_task(void *arg)
{
    (void)arg;
    TickType_t t = xTaskGetTickCount();
    for (;;)
    {
        HAL_GPIO_TogglePin(GPIOC, LED_Pin);
        vTaskDelayUntil(&t, pdMS_TO_TICKS(200));
    }
}

/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{

  /* USER CODE BEGIN 1 */

  /* USER CODE END 1 */

  /* MCU Configuration--------------------------------------------------------*/

  /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
  HAL_Init();

  /* USER CODE BEGIN Init */

  /* USER CODE END Init */

  /* Configure the system clock */
  SystemClock_Config();

  /* USER CODE BEGIN SysInit */

  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_DMA_Init();
  MX_TIM3_Init();
  MX_USART2_UART_Init();
  /* USER CODE BEGIN 2 */
  HAL_TIM_PWM_Start(&htim3, TIM_CHANNEL_1);
  HAL_TIM_PWM_Start(&htim3, TIM_CHANNEL_2);

  motors_off();

  q_uart2_rx   = xQueueCreate(RX_QUEUE_LEN, sizeof(uint8_t));
  sem_uart2_tx = xSemaphoreCreateBinary();
  mtx_uart2_tx = xSemaphoreCreateMutex();

  HAL_UART_Receive_IT(&huart2, &uart2_rx_byte, 1);

  xTaskCreate(shell_task, "shell", 512, NULL, 4, NULL);
  xTaskCreate(blink_task, "blink", 128, NULL, 1, NULL);

  /* USER CODE END 2 */

  /* Call init function for freertos objects (in cmsis_os2.c) */
  MX_FREERTOS_Init();
  osKernelStart();

  /* We should never get here as control is now taken by the scheduler */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */
  }
  /* USER CODE END 3 */
}

/**
  * @brief System Clock Configuration
  * @retval None
  */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  /** Configure the main internal regulator output voltage
  */
  __HAL_RCC_PWR_CLK_ENABLE();
  __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1);

  /** Initializes the RCC Oscillators according to the specified parameters
  * in the RCC_OscInitTypeDef structure.
  */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSI;
  RCC_OscInitStruct.HSIState = RCC_HSI_ON;
  RCC_OscInitStruct.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSI;
  RCC_OscInitStruct.PLL.PLLM = 8;
  RCC_OscInitStruct.PLL.PLLN = 100;
  RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV2;
  RCC_OscInitStruct.PLL.PLLQ = 4;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }

  /** Initializes the CPU, AHB and APB buses clocks
  */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV2;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_3) != HAL_OK)
  {
    Error_Handler();
  }
}

/* USER CODE BEGIN 4 */

/* USER CODE END 4 */

/**
  * @brief  Period elapsed callback in non blocking mode
  * @note   This function is called  when TIM1 interrupt took place, inside
  * HAL_TIM_IRQHandler(). It makes a direct call to HAL_IncTick() to increment
  * a global variable "uwTick" used as application time base.
  * @param  htim : TIM handle
  * @retval None
  */
void HAL_TIM_PeriodElapsedCallback(TIM_HandleTypeDef *htim)
{
  /* USER CODE BEGIN Callback 0 */

  /* USER CODE END Callback 0 */
  if (htim->Instance == TIM1)
  {
    HAL_IncTick();
  }
  /* USER CODE BEGIN Callback 1 */

  /* USER CODE END Callback 1 */
}

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  /* User can add his own implementation to report the HAL error return state */
  __disable_irq();
  while (1)
  {
  }
  /* USER CODE END Error_Handler_Debug */
}
#ifdef USE_FULL_ASSERT
/**
  * @brief  Reports the name of the source file and the source line number
  *         where the assert_param error has occurred.
  * @param  file: pointer to the source file name
  * @param  line: assert_param error line source number
  * @retval None
  */
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  /* User can add his own implementation to report the file name and line number,
     ex: printf("Wrong parameters value: file %s on line %d\r\n", file, line) */
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
