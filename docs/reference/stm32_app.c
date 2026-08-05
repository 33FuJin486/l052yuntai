/**
 * app.c - 云台主控程序
 *
 * 功能概述:
 *   1. 摇杆模式: 通过ADC读取摇杆(X=CH8, Y=CH9)控制步进电机
 *   2. 视觉模式: 通过串口接收PC端偏移量(X/Y), PID闭环控制电机
 *   3. 按摇杆按键可在两种模式间切换
 *   4. 蜂鸣器反馈: 开机/切模式/断联 四种提示音
 *
 * 修改指南:
 *   - 改PID参数       -> 搜 "pid_yaw" / "pid_pitch" 结构体
 *   - 改摇杆死区       -> 搜 "Map_To_Speed", 改 1400/2700 阈值
 *   - 改蜂鸣器音调     -> 搜 "Buzzer_Task", 改 BUZZER_FREQ() 内频率值
 *   - 改串口超时       -> 搜 PC_COMM_TIMEOUT_MS
 *   - 改OLED显示内容   -> 搜 "OLED_print"
 *   - 改电机最大速度   -> 搜 MAX_STEP_FREQ (在 stepper_ctrl.h 中)
 *   - 改模式切换逻辑   -> 搜 "pc_control_mode"
 */

/* ================================================================
   第1部分: 硬件抽象层 — 头文件、句柄、宏定义、枚举、全局变量
   ================================================================ */

#include "app.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "stepper_ctrl.h"
#include "sh1106.h"
#include "draw.h"
#include "font.h"

/* ================= 外部句柄 & 硬件宏 ================= */
extern TIM_HandleTypeDef htim2;
extern UART_HandleTypeDef huart1;
extern ADC_HandleTypeDef hadc;
extern void OLED_BUFF_fill(uint8_t temp);

#define BUZZER_CHANNEL    TIM_CHANNEL_1          // 蜂鸣器PWM通道
#define READ_JOY_KEY()    HAL_GPIO_ReadPin(GPIOC, GPIO_PIN_15)  // 摇杆按键
#define PC_COMM_TIMEOUT_MS 500                   // PC通信超时(ms), 改这里

/* ================= 蜂鸣器事件枚举 =================
   BOOT  = 开机欢迎音 (C5→E5→G5→C6 四连音)
   MODE_JOY = 切到摇杆模式 (高→低 两音)
   MODE_PC  = 切到视觉模式 (低→高 三音上行)
   ERROR    = 视觉断联警告 (两短低音)
  如需新增音效: 在此枚举加一项, 再到 Buzzer_Task() 加 case */
typedef enum {
    BUZZER_NONE = 0,
    BUZZER_BOOT,
    BUZZER_MODE_JOY,
    BUZZER_MODE_PC,
    BUZZER_LINK_OK,
    BUZZER_ERROR
} Buzzer_Event_t;

/* ================= 私有变量 ================= */
static Buzzer_Event_t buzzer_event      = BUZZER_NONE;
static uint32_t       buzzer_start_tick = 0;
static uint8_t        buzzer_lost_alarm = 0;   // 断联只响一次
static char           last_rx_msg[32]   = "No Data";

uint8_t  rx_dma_buf[64];
char     rx_buf[64];
uint8_t  rx_flag      = 0;
uint32_t last_rx_tick  = 0;
uint8_t  pc_control_mode = 0;   // 0=摇杆模式, 1=视觉模式
float    target_yaw_speed   = 0.0f;
float    target_pitch_speed  = 0.0f;

/* ================= 函数前向声明 ================= */
static void Buzzer_Task(void);

/* ================================================================
   第2部分: 核心算法模块 — PID控制器、蜂鸣器驱动、工具函数
   ================================================================ */

/* ================= 蜂鸣器播放接口 =================
   用法: Buzzer_Play(BUZZER_BOOT);  // 触发一次音效, 非阻塞 */
static void Buzzer_Play(Buzzer_Event_t event)
{
    buzzer_event = event;
    buzzer_start_tick = HAL_GetTick();
}

/* ================= PID 控制器 =================
   改Kp/Ki/Kd/死区/滤波/限幅 都在下面两个结构体里
   不需要动 PID_Calc() 和 PID_Reset() */
typedef struct {
    float Kp, Ki, Kd;           // PID增益 — 改这里!
    float error_last;           // 上次误差 (内部用)
    float integral;             // 积分累计 (内部用)
    float max_out;              // 输出限幅
    float max_integral;         // 积分限幅 (防饱和)
    float deadzone;             // 死区 — 误差小于此值视为0
    float filter_alpha;         // 低通滤波系数 (0~1, 越小越平滑)
    float filtered_error;       // 滤波后误差 (内部用)
} PID_Controller_t;

/* === 偏航(Yaw) PID参数 — 改这里! === */
PID_Controller_t pid_yaw = {
    0.8f,   0.0f,   0.1f,      // Kp, Ki, Kd
    0.0f,   0.0f,               // error_last, integral (初始=0)
    MAX_STEP_FREQ,               // max_out
    1000.0f,                     // max_integral
    40.0f,                       // deadzone: 误差<40像素时不动作
    0.6f,                        // filter_alpha: 0.6=中等平滑
    0.0f                         // filtered_error (初始=0)
};

/* === 俯仰(Pitch) PID参数 — 改这里! === */
PID_Controller_t pid_pitch = {
    0.8f,   0.0f,   0.1f,      // Kp, Ki, Kd
    0.0f,   0.0f,               // error_last, integral
    MAX_STEP_FREQ,               // max_out
    1000.0f,                     // max_integral
    40.0f,                       // deadzone
    0.6f,                        // filter_alpha
    0.0f                         // filtered_error
};

static float PID_Calc(PID_Controller_t *pid, float raw_error)
{
    /* 死区: 小误差直接归零, 避免电机微抖 */
    if (raw_error > -pid->deadzone && raw_error < pid->deadzone)
        raw_error = 0.0f;

    /* 低通滤波: 平滑突变 */
    pid->filtered_error = pid->filter_alpha * raw_error
                        + (1.0f - pid->filter_alpha) * pid->filtered_error;

    float error = pid->filtered_error;

    /* 积分 + 限幅 */
    pid->integral += error;
    if (pid->integral >  pid->max_integral) pid->integral =  pid->max_integral;
    if (pid->integral < -pid->max_integral) pid->integral = -pid->max_integral;

    /* 微分 */
    float derivative = error - pid->error_last;

    /* PID输出 */
    float out = pid->Kp * error
              + pid->Ki * pid->integral
              + pid->Kd * derivative;

    pid->error_last = error;

    /* 输出限幅 */
    if (out >  pid->max_out) out =  pid->max_out;
    if (out < -pid->max_out) out = -pid->max_out;

    return out;
}

static void PID_Reset(PID_Controller_t *pid)
{
    pid->error_last     = 0.0f;
    pid->integral       = 0.0f;
    pid->filtered_error = 0.0f;
}

/* ================= 蜂鸣器底层驱动 ================= */
/* 设置PWM频率: BUZZER_FREQ(523) = C5音
   频率对照: C5=523, D5=587, E5=659, F5=698, G5=784, A5=880, B5=988, C6=1047 */
static void BUZZER_FREQ(uint32_t freq)
{
    uint32_t timer_clk = HAL_RCC_GetPCLK1Freq();
    /* STM32 APB分频≠1时定时器时钟翻倍 */
    if ((RCC->CFGR & RCC_CFGR_PPRE1) != RCC_CFGR_PPRE1_DIV1)
        timer_clk *= 2;
    /* PWM频率: F = TIM_CLK / ((PSC+1) * (ARR+1)) */
    uint32_t arr = timer_clk / ((htim2.Init.Prescaler + 1) * freq) - 1;
    __HAL_TIM_SET_AUTORELOAD(&htim2, arr);
    __HAL_TIM_SET_COMPARE(&htim2, BUZZER_CHANNEL, arr / 2);  // 50%占空比
    HAL_TIM_PWM_Start(&htim2, BUZZER_CHANNEL);
}

/* 静音: 把占空比拉0 (不停止PWM, 方便音符间平滑切换) */
static void BUZZER_OFF(void)
{
    __HAL_TIM_SET_COMPARE(&htim2, BUZZER_CHANNEL, 0);
}

/* ================= 工具函数 ================= */
/* 整数转字符串 (简易实现, 不用sprintf省空间) */
static char* itoa_s(uint32_t num, char* buf)
{
    uint8_t i = 0;
    if (num == 0) { buf[0] = '0'; buf[1] = '\0'; return buf; }
    while (num > 0 && i < 10) { buf[i++] = '0' + (num % 10); num /= 10; }
    for (uint8_t j = 0; j < i / 2; j++) {
        char t = buf[j];
        buf[j] = buf[i - 1 - j];
        buf[i - 1 - j] = t;
    }
    buf[i] = '\0';
    return buf;
}

/* ================================================================
   第3部分: 通信与输入模块 — 串口收发、ADC读取
   ================================================================ */

/* ================= 串口发送测试 =================
   VOFA+ 测试输出
   格式:
   yaw_speed,pitch_speed,mode
================================================= */

static void UART_Send_Test(void)
{
    char tx_buf[64];

    sprintf(tx_buf,
            "%.2f,%.2f,%d\r\n",
            target_yaw_speed,
            target_pitch_speed,
            pc_control_mode);


    HAL_UART_Transmit(&huart1,
                      (uint8_t*)tx_buf,
                      strlen(tx_buf),
                      100);
}

/* ================= 串口接收中断回调 =================
   PC新协议:

       目标存在:
       [x,y]\n
       例如:
       [120,-35]

       目标丢失:
       [9999,9999]\n

   不再使用flag标志位:
       9999,9999 代表视觉端没有找到目标
       0,0        是合法坐标
*/
void HAL_UARTEx_RxEventCallback(UART_HandleTypeDef *huart, uint16_t Size)
{
    if (huart->Instance == USART1) {
        if (Size < sizeof(rx_buf)) {
            memcpy(rx_buf, rx_dma_buf, Size);
            rx_buf[Size] = '\0';
            rx_flag = 1;
        }
        HAL_UARTEx_ReceiveToIdle_DMA(&huart1, rx_dma_buf, sizeof(rx_dma_buf));
        __HAL_DMA_DISABLE_IT(huart1.hdmarx, DMA_IT_HT);
    }
}

/* ================= ADC 读取 ================= */
static uint32_t ADC_Read(uint32_t channel)
{
    HAL_ADC_Stop(&hadc);
    ADC1->CHSELR = 0;

    ADC_ChannelConfTypeDef sCfg = {0};
    sCfg.Channel = channel;
    sCfg.Rank = ADC_RANK_CHANNEL_NUMBER;
    HAL_ADC_ConfigChannel(&hadc, &sCfg);

    if (HAL_ADC_Start(&hadc) != HAL_OK) return 0xFFFF;
    if (HAL_ADC_PollForConversion(&hadc, 10) != HAL_OK) {
        HAL_ADC_Stop(&hadc);
        return 0xFFFF;
    }
    uint32_t val = HAL_ADC_GetValue(&hadc);
    HAL_ADC_Stop(&hadc);
    return val;
}

/* ADC值 → 速度映射
   摇杆电位器中位≈2048, 死区1400~2700
   改死区: 改下面两个阈值 (1400 / 2700) */
static float Map_To_Speed(uint32_t adc_val)
{
    if (adc_val < 1400) {                         // 左/下死区阈值
        float ratio = (1400.0f - adc_val) / 1400.0f;
        return -ratio * MAX_STEP_FREQ;
    } else if (adc_val > 2700) {                  // 右/上死区阈值
        float ratio = ((float)adc_val - 2700.0f) / (4095.0f - 2700.0f);
        return ratio * MAX_STEP_FREQ;
    }
    return 0.0f;  // 死区内 = 停止
}

/* ================================================================
   第4部分: 自动测试模式 — 状态机驱动的电机自检
   ================================================================ */

/* ================= 自动测试模式 =================
   enable_test_mode: 0=正常控制, 1=自动测试
   test_stage:       0=仅水平(Yaw), 1=仅垂直(Pitch), 2=双轴联动

   测试节拍（完全对称）:
   正转 1000ms -> 停止 300ms -> 反转 1000ms -> 停止 700ms -> 循环

   关键设计:
   1. 测试模式拥有唯一的电机控制权，正常模式不会再抢写速度。
   2. 只在阶段变化时调用 Stepper_SetSpeed，避免重复重配底层定时器。
   3. test_stage 运行中被修改时，会立即停机并从正转阶段重新开始。
   4. 退出测试模式时会先安全停车，再交还正常模式控制权。
   ================================================================ */
#define TEST_SPEED_RATIO       0.20f
#define TEST_FORWARD_MS        1000U
#define TEST_BRAKE1_MS          300U
#define TEST_REVERSE_MS        1000U
#define TEST_BRAKE2_MS          700U

uint8_t enable_test_mode = 0;  // 0: 正常模式, 1: 开启自动测试
uint8_t test_stage = 2;        // 0: 水平, 1: 垂直, 2: 双轴联动

typedef enum {
    TEST_PHASE_FORWARD = 0,
    TEST_PHASE_BRAKE_1,
    TEST_PHASE_REVERSE,
    TEST_PHASE_BRAKE_2
} Auto_Test_Phase_t;

static Auto_Test_Phase_t test_phase = TEST_PHASE_FORWARD;
static uint32_t test_phase_tick = 0;
static uint8_t test_is_running = 0;
static uint8_t test_last_stage = 0xFF;

/* 根据测试轴和当前速度，一次性下发双轴速度 */
static void Auto_Test_ApplySpeed(float speed)
{
    float yaw_out = 0.0f;
    float pitch_out = 0.0f;

    switch (test_stage) {
        case 0:                         // 仅水平轴
            yaw_out = speed;
            break;

        case 1:                         // 仅垂直轴
            pitch_out = speed;
            break;

        case 2:                         // 双轴同向联动
            yaw_out = speed;
            pitch_out = speed;
            break;

        default:                        // 参数非法时立即停车
            yaw_out = 0.0f;
            pitch_out = 0.0f;
            break;
    }

    Stepper_SetSpeed(yaw_out, pitch_out);
}

/* 退出测试模式或重新进入测试时，统一复位状态机 */
static void Auto_Test_Reset(void)
{
    if (test_is_running) {
        Stepper_SetSpeed(0.0f, 0.0f);
    }

    test_phase = TEST_PHASE_FORWARD;
    test_phase_tick = 0;
    test_is_running = 0;
    test_last_stage = 0xFF;
}

static void Auto_Test_Task(void)
{
    uint32_t now = HAL_GetTick();
    float safe_speed = MAX_STEP_FREQ * TEST_SPEED_RATIO;

    if (!enable_test_mode) {
        Auto_Test_Reset();
        return;
    }

    /* 第一次进入，或测试轴被修改：先停车，再从正转阶段重新开始 */
    if (!test_is_running || test_last_stage != test_stage) {
        Stepper_SetSpeed(0.0f, 0.0f);
        test_phase = TEST_PHASE_FORWARD;
        test_phase_tick = now;
        test_is_running = 1;
        test_last_stage = test_stage;
        Auto_Test_ApplySpeed(safe_speed);
        return;
    }

    /* 非阻塞状态机：只有到达阶段时间才切换一次 */
    switch (test_phase) {
        case TEST_PHASE_FORWARD:
            if ((uint32_t)(now - test_phase_tick) >= TEST_FORWARD_MS) {
                test_phase = TEST_PHASE_BRAKE_1;
                test_phase_tick = now;
                Auto_Test_ApplySpeed(0.0f);
            }
            break;

        case TEST_PHASE_BRAKE_1:
            if ((uint32_t)(now - test_phase_tick) >= TEST_BRAKE1_MS) {
                test_phase = TEST_PHASE_REVERSE;
                test_phase_tick = now;
                Auto_Test_ApplySpeed(-safe_speed);
            }
            break;

        case TEST_PHASE_REVERSE:
            if ((uint32_t)(now - test_phase_tick) >= TEST_REVERSE_MS) {
                test_phase = TEST_PHASE_BRAKE_2;
                test_phase_tick = now;
                Auto_Test_ApplySpeed(0.0f);
            }
            break;

        case TEST_PHASE_BRAKE_2:
        default:
            if ((uint32_t)(now - test_phase_tick) >= TEST_BRAKE2_MS) {
                test_phase = TEST_PHASE_FORWARD;
                test_phase_tick = now;
                Auto_Test_ApplySpeed(safe_speed);
            }
            break;
    }
}

/* ================================================================
   第5部分: 主程序入口 — 初始化、主循环、蜂鸣器任务
   ================================================================ */

/* ================= 主程序: 初始化 ================= */
void App_Init(void)
{
    Stepper_Init();
    Stepper_SetSpeed(0.0f, 0.0f);

    OLED_init();
    OLED_BUFF_fill(0);
    OLED_print(0, 0,  "YunTai v2", Font_8X16, Dot_set, Dot_clear);
    OLED_print(0, 16, "Init OK",   Font_8X16, Dot_set, Dot_clear);
    OLED_refresh();

    Buzzer_Play(BUZZER_BOOT);  // 开机欢迎音

    HAL_UARTEx_ReceiveToIdle_DMA(&huart1, rx_dma_buf, sizeof(rx_dma_buf));
    __HAL_DMA_DISABLE_IT(huart1.hdmarx, DMA_IT_HT);
}

/* ================= 主程序: 主循环 (50ms周期) ================= */
void App_Task(void)
{
    uint32_t now = HAL_GetTick();

    /* ===== 高频非阻塞任务: 串口TX + 蜂鸣器 (每循环调用) ===== */
    static uint32_t uart_tx_tick = 0;

    if(now - uart_tx_tick > 200)
    {
        uart_tx_tick = now;
        UART_Send_Test();
    }
    Buzzer_Task();

    static uint32_t last_tick = 0;

    /* ===== PC视觉通讯: 解析串口数据 ===== */
    if (rx_flag) {
        strncpy(last_rx_msg, rx_buf, 16);
        last_rx_msg[16] = '\0';

        int err_x = 0;
        int err_y = 0;

        /*
         * 新协议解析:
         * [x,y]
         *
         * 示例:
         * [120,-35]
         */
        if (sscanf(rx_buf, "[%d,%d]", &err_x, &err_y) == 2) {

            last_rx_tick = now;

            /*
             * 视觉端发送9999表示目标丢失
             * 避免与真实中心坐标0,0冲突
             */
            if (err_x == 9999 && err_y == 9999) {

                target_yaw_speed  = 0.0f;
                target_pitch_speed = 0.0f;

                PID_Reset(&pid_yaw);
                PID_Reset(&pid_pitch);

            } else {

                /* 有目标:
                   X/Y误差进入PID
                */

                target_yaw_speed =
                    PID_Calc(&pid_yaw, -(float)err_x);

                target_pitch_speed =
                    PID_Calc(&pid_pitch, -(float)err_y);
            }
        }
        rx_flag = 0;
    }

    /* ---------- 50ms定时: 控制循环频率 ---------- */
    if (now - last_tick < 50) return;
    last_tick = now;

    /* ===== 正常模式输入与保护 =====
       测试模式开启时不处理模式按键，也不执行视觉断联停车，
       避免任何其他逻辑覆盖自动测试下发的速度。 */
    static uint8_t key_state = 0;

    if (!enable_test_mode) {
        /* 摇杆按键: 模式切换 */
        if (READ_JOY_KEY() == GPIO_PIN_RESET) {
            if (key_state == 0) {
                key_state = 1;
                pc_control_mode = !pc_control_mode;  // 翻转模式
                Buzzer_Play(pc_control_mode ? BUZZER_MODE_PC : BUZZER_MODE_JOY);

                /* 切换时清零所有输出 */
                target_yaw_speed = 0.0f;
                target_pitch_speed = 0.0f;
                Stepper_SetSpeed(0.0f, 0.0f);
                PID_Reset(&pid_yaw);
                PID_Reset(&pid_pitch);
            }
        } else {
            key_state = 0;
        }

        /* 视觉模式: 断联保护 */
        if (pc_control_mode == 1) {
            if ((uint32_t)(now - last_rx_tick) > PC_COMM_TIMEOUT_MS) {
                target_yaw_speed = 0.0f;
                target_pitch_speed = 0.0f;
                PID_Reset(&pid_yaw);
                PID_Reset(&pid_pitch);

                if (buzzer_lost_alarm == 0) {   // 只响一次
                    Buzzer_Play(BUZZER_ERROR);
                    buzzer_lost_alarm = 1;
                }
            } else {
                buzzer_lost_alarm = 0;          // 恢复后允许下次报警
            }
        }
    } else {
        /* 测试模式期间释放按键锁存，退出测试后可正常检测下一次按键 */
        key_state = 0;
        buzzer_lost_alarm = 0;
    }

    /* ===== 电机动力输出：测试模式与正常模式二选一，禁止重复抢写 ===== */
    if (enable_test_mode) {
        /* 自动测试独占电机控制权 */
        Auto_Test_Task();
    } else {
        /* 先复位测试状态；仅首次退出测试时会执行停车 */
        Auto_Test_Reset();

        if (pc_control_mode == 1) {
            /* 视觉模式: 输出PID计算结果 */
            Stepper_SetSpeed(target_yaw_speed, target_pitch_speed);
        } else {
            /* 摇杆模式: 读ADC + 映射速度 */
            uint32_t x_raw = ADC_Read(ADC_CHANNEL_8);
            uint32_t y_raw = ADC_Read(ADC_CHANNEL_9);
            if (x_raw != 0xFFFF && y_raw != 0xFFFF) {
                Stepper_SetSpeed(Map_To_Speed(x_raw), Map_To_Speed(y_raw));
            } else {
                /* ADC读取失败时安全停车，禁止沿用旧速度 */
                Stepper_SetSpeed(0.0f, 0.0f);
            }
        }
    }
    /* ===== OLED显示 (每3次刷新一次, 约150ms) ===== */
    static uint8_t disp = 0;
    if (++disp >= 3) {
        disp = 0;
        OLED_BUFF_fill(0);
        char b[16];

        if (enable_test_mode) {
            /* 自动测试模式显示 */
            OLED_print(0,  0, "Mode: AUTO TEST", Font_8X16, Dot_set, Dot_clear);

            if (test_stage == 0)
                OLED_print(0, 16, "Axis: YAW", Font_8X16, Dot_set, Dot_clear);
            else if (test_stage == 1)
                OLED_print(0, 16, "Axis: PITCH", Font_8X16, Dot_set, Dot_clear);
            else if (test_stage == 2)
                OLED_print(0, 16, "Axis: BOTH", Font_8X16, Dot_set, Dot_clear);
            else
                OLED_print(0, 16, "Axis: ERROR", Font_8X16, Dot_set, Dot_clear);

            if (test_phase == TEST_PHASE_FORWARD)
                OLED_print(0, 32, "Run: FORWARD", Font_8X16, Dot_set, Dot_clear);
            else if (test_phase == TEST_PHASE_REVERSE)
                OLED_print(0, 32, "Run: REVERSE", Font_8X16, Dot_set, Dot_clear);
            else
                OLED_print(0, 32, "Run: STOP", Font_8X16, Dot_set, Dot_clear);

            sprintf(b, "Spd:%d", (int)(MAX_STEP_FREQ * TEST_SPEED_RATIO));
            OLED_print(0, 48, b, Font_8X16, Dot_set, Dot_clear);
        } else if (pc_control_mode == 1) {
            /* 视觉模式显示 */
            OLED_print(0,  0, "Mode: PC TRACK", Font_8X16, Dot_set, Dot_clear);
            OLED_print(0, 16, (now - last_rx_tick < PC_COMM_TIMEOUT_MS)
                              ? "Link: OK   " : "Link: LOST ",
                       Font_8X16, Dot_set, Dot_clear);
            OLED_print(0, 32, "Y_Spd:", Font_8X16, Dot_set, Dot_clear);
            sprintf(b, "%d", (int)target_yaw_speed);
            OLED_print(48, 32, b, Font_8X16, Dot_set, Dot_clear);
            OLED_print(0, 48, "P_Spd:", Font_8X16, Dot_set, Dot_clear);
            sprintf(b, "%d", (int)target_pitch_speed);
            OLED_print(48, 48, b, Font_8X16, Dot_set, Dot_clear);
        } else {
            /* 摇杆模式显示 */
            OLED_print(0,  0, "Mode: JOYSTICK", Font_8X16, Dot_set, Dot_clear);
            uint32_t x_raw_disp = ADC_Read(ADC_CHANNEL_8);
            uint32_t y_raw_disp = ADC_Read(ADC_CHANNEL_9);
            OLED_print(0,  24, "X_ADC:", Font_8X16, Dot_set, Dot_clear);
            OLED_print(48, 24, itoa_s(x_raw_disp, b), Font_8X16, Dot_set, Dot_clear);
            OLED_print(0,  40, "Y_ADC:", Font_8X16, Dot_set, Dot_clear);
            OLED_print(48, 40, itoa_s(y_raw_disp, b), Font_8X16, Dot_set, Dot_clear);
        }
        OLED_refresh();
    }
}

/* ================================================================
   蜂鸣器音效任务 — 状态机实现, 非阻塞
   所有音效的时间/频率在这里改!
   t = 从触发开始经过的毫秒数
   ================================================================ */
static void Buzzer_Task(void)
{
    uint32_t t = HAL_GetTick() - buzzer_start_tick;

    switch (buzzer_event) {

    /* ---- 开机欢迎音: G5→B5→E6→D6→B5→G5→E6 ---- */
    case BUZZER_BOOT:

        if      (t < 120)  BUZZER_FREQ(784);     // G5
        else if (t < 180)  BUZZER_OFF();

        else if (t < 320)  BUZZER_FREQ(988);     // B5
        else if (t < 380)  BUZZER_OFF();

        else if (t < 560)  BUZZER_FREQ(1319);    // E6
        else if (t < 650)  BUZZER_OFF();

        else if (t < 820)  BUZZER_FREQ(1175);    // D6
        else if (t < 900)  BUZZER_OFF();

        else if (t < 1080) BUZZER_FREQ(988);     // B5
        else if (t < 1160) BUZZER_OFF();

        else if (t < 1350) BUZZER_FREQ(784);     // G5
        else if (t < 1450) BUZZER_OFF();

        else if (t < 1900) BUZZER_FREQ(1319);    // E6 尾音

        else
        {
            BUZZER_OFF();
            buzzer_event = BUZZER_NONE;
        }

        break;
    /* ---- 切摇杆模式: 高→低 (两音下行) ---- */
    case BUZZER_MODE_JOY:
        if      (t < 150) BUZZER_FREQ(784);     // G5
        else if (t < 350) BUZZER_FREQ(523);     // C5
        else { BUZZER_OFF(); buzzer_event = BUZZER_NONE; }
        break;

    /* ---- 切视觉模式: 低→高 (三音上行) ---- */
    case BUZZER_MODE_PC:
        if      (t < 150) BUZZER_FREQ(523);     // C5
        else if (t < 300) BUZZER_FREQ(659);     // E5
        else if (t < 500) BUZZER_FREQ(1047);    // C6
        else { BUZZER_OFF(); buzzer_event = BUZZER_NONE; }
        break;

    /* ---- 断联警告: 两短促低音 ---- */
    case BUZZER_ERROR:
        if      (t < 150) BUZZER_FREQ(330);     // E4 (低沉)
        else if (t < 300) BUZZER_OFF();
        else if (t < 450) BUZZER_FREQ(330);
        else { BUZZER_OFF(); buzzer_event = BUZZER_NONE; }
        break;

    default:
        BUZZER_OFF();
        break;
    }
}