<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.description') }}</p>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else>
      <div class="card budget-card">
        <div class="budget-row">
          <label for="budget-slider" class="budget-label">{{ t('restocking.budgetSlider') }}</label>
          <span class="budget-value">{{ currencySymbol }}{{ budget.toLocaleString('en-US') }}</span>
        </div>
        <input
          id="budget-slider"
          v-model.number="budget"
          class="budget-slider"
          type="range"
          min="0"
          :max="sliderMax"
          :step="SLIDER_STEP"
          :aria-label="t('restocking.budgetSlider')"
          :aria-valuetext="`${currencySymbol}${budget.toLocaleString('en-US')}`"
        />
        <div class="budget-scale">
          <span>{{ currencySymbol }}0</span>
          <span>{{ currencySymbol }}{{ sliderMax.toLocaleString('en-US') }}</span>
        </div>
      </div>

      <div class="stats-grid">
        <div class="stat-card info">
          <div class="stat-label">{{ t('restocking.budget') }}</div>
          <div class="stat-value">{{ currencySymbol }}{{ budget.toLocaleString('en-US') }}</div>
        </div>
        <div class="stat-card success">
          <div class="stat-label">{{ t('restocking.plannedSpend') }}</div>
          <div class="stat-value">{{ currencySymbol }}{{ plannedSpend.toLocaleString('en-US') }}</div>
        </div>
        <div class="stat-card warning">
          <div class="stat-label">{{ t('restocking.remainingBudget') }}</div>
          <div class="stat-value">{{ currencySymbol }}{{ remainingBudget.toLocaleString('en-US') }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('restocking.itemsToOrder') }}</div>
          <div class="stat-value">{{ recommendations.length }}</div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.recommendedItems') }} ({{ recommendations.length }})</h3>
          <button
            type="button"
            class="place-order-btn"
            :disabled="recommendations.length === 0 || submitting"
            @click="placeOrder"
          >
            {{ submitting ? t('restocking.placing') : t('restocking.placeOrder') }}
          </button>
        </div>

        <div v-if="orderResult" class="order-success" role="status">
          <strong>{{ t('restocking.orderPlaced') }}</strong>
          {{ t('restocking.orderSummary', {
            number: orderResult.order_number,
            total: `${currencySymbol}${orderResult.total_value.toLocaleString('en-US')}`,
            days: orderResult.lead_time_days
          }) }}
        </div>
        <div v-if="submitError" class="error" role="alert">{{ submitError }}</div>

        <div v-if="recommendations.length === 0" class="empty-state">
          {{ t('restocking.emptyState') }}
        </div>
        <div v-else class="table-container">
          <table>
            <thead>
              <tr>
                <th>{{ t('restocking.table.sku') }}</th>
                <th>{{ t('restocking.table.item') }}</th>
                <th>{{ t('restocking.table.category') }}</th>
                <th>{{ t('restocking.table.trend') }}</th>
                <th>{{ t('restocking.table.onHand') }}</th>
                <th>{{ t('restocking.table.forecast') }}</th>
                <th>{{ t('restocking.table.orderQty') }}</th>
                <th>{{ t('restocking.table.unitCost') }}</th>
                <th>{{ t('restocking.table.lineTotal') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="rec in recommendations" :key="rec.sku">
                <td><strong>{{ rec.sku }}</strong></td>
                <td>{{ rec.name }}</td>
                <td>{{ rec.category }}</td>
                <td>
                  <span :class="['badge', rec.trend]">{{ t(`trends.${rec.trend}`) }}</span>
                </td>
                <td>{{ rec.onHand }}</td>
                <td>{{ rec.forecasted }}</td>
                <td><strong>{{ rec.quantity }}</strong></td>
                <td>{{ currencySymbol }}{{ rec.unitCost.toLocaleString('en-US', { minimumFractionDigits: 2 }) }}</td>
                <td><strong>{{ currencySymbol }}{{ rec.lineTotal.toLocaleString('en-US') }}</strong></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api'
import { useI18n } from '../composables/useI18n'

const SLIDER_STEP = 100
// Slider maximum is rounded up to this increment so the range end is a clean number
const MAX_ROUND_TO = 1000

export default {
  name: 'Restocking',
  setup() {
    const { t, currentCurrency } = useI18n()

    const currencySymbol = computed(() => (currentCurrency.value === 'JPY' ? '¥' : '$'))

    const loading = ref(true)
    const error = ref(null)
    const forecasts = ref([])
    const inventoryItems = ref([])
    const budget = ref(0)

    const submitting = ref(false)
    const submitError = ref(null)
    const orderResult = ref(null)

    // Total on-hand per SKU. An SKU can appear in several warehouses, so sum the rows.
    const onHandBySku = computed(() => {
      const map = new Map()
      for (const item of inventoryItems.value) {
        map.set(item.sku, (map.get(item.sku) || 0) + (item.quantity_on_hand || 0))
      }
      return map
    })

    // Every forecast with a positive shortfall, largest shortfall first
    const shortfalls = computed(() => {
      return forecasts.value
        .map(f => {
          const onHand = onHandBySku.value.get(f.item_sku) || 0
          const quantity = Math.max(0, f.forecasted_demand - onHand)
          return {
            sku: f.item_sku,
            name: f.item_name,
            category: f.category,
            trend: f.trend,
            onHand,
            forecasted: f.forecasted_demand,
            quantity,
            unitCost: f.unit_cost,
            lineTotal: quantity * f.unit_cost
          }
        })
        .filter(r => r.quantity > 0)
        .sort((a, b) => b.quantity - a.quantity)
    })

    // Cost to cover every shortfall; drives the slider range
    const totalShortfallCost = computed(() =>
      shortfalls.value.reduce((sum, r) => sum + r.lineTotal, 0)
    )

    const sliderMax = computed(() =>
      Math.max(MAX_ROUND_TO, Math.ceil(totalShortfallCost.value / MAX_ROUND_TO) * MAX_ROUND_TO)
    )

    // Greedy fill: walk shortfalls in priority order, take an item only if its whole
    // line fits in the remaining budget, otherwise skip it and try the next (cheaper) one.
    const recommendations = computed(() => {
      let remaining = budget.value
      const picked = []
      for (const r of shortfalls.value) {
        if (remaining >= r.lineTotal) {
          picked.push(r)
          remaining -= r.lineTotal
        }
      }
      return picked
    })

    const plannedSpend = computed(() =>
      recommendations.value.reduce((sum, r) => sum + r.lineTotal, 0)
    )
    const remainingBudget = computed(() => budget.value - plannedSpend.value)

    const loadData = async () => {
      try {
        loading.value = true
        error.value = null
        // Restocking is company-wide, so inventory is loaded without warehouse/category filters
        const [forecastData, inventoryData] = await Promise.all([
          api.getDemandForecasts(),
          api.getInventory()
        ])
        forecasts.value = forecastData
        inventoryItems.value = inventoryData
        // Default to about half of the full-coverage cost, snapped to the slider step
        budget.value = Math.min(
          sliderMax.value,
          Math.round(sliderMax.value / 2 / SLIDER_STEP) * SLIDER_STEP
        )
      } catch (err) {
        error.value = t('restocking.loadFailed') + err.message
      } finally {
        loading.value = false
      }
    }

    const placeOrder = async () => {
      if (recommendations.value.length === 0 || submitting.value) return
      submitting.value = true
      submitError.value = null
      orderResult.value = null
      try {
        orderResult.value = await api.createRestockOrder({
          budget: budget.value,
          items: recommendations.value.map(r => ({ sku: r.sku, quantity: r.quantity }))
        })
      } catch (err) {
        // Prefer the backend's explanation (400/404 carry a `detail` field)
        submitError.value = err.response?.data?.detail || t('restocking.placeFailed')
      } finally {
        submitting.value = false
      }
    }

    onMounted(loadData)

    return {
      t,
      currencySymbol,
      loading,
      error,
      budget,
      sliderMax,
      SLIDER_STEP,
      recommendations,
      plannedSpend,
      remainingBudget,
      submitting,
      submitError,
      orderResult,
      placeOrder
    }
  }
}
</script>

<style scoped>
.budget-card {
  padding: 1.5rem;
  margin-bottom: 1.5rem;
}

.budget-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 0.75rem;
}

.budget-label {
  font-size: 0.875rem;
  font-weight: 600;
  color: #64748b;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.budget-value {
  font-size: 1.5rem;
  font-weight: 700;
  color: #0f172a;
}

.budget-slider {
  width: 100%;
  accent-color: #2563eb;
  cursor: pointer;
}

.budget-slider:focus-visible {
  outline: 2px solid #2563eb;
  outline-offset: 2px;
}

.budget-scale {
  display: flex;
  justify-content: space-between;
  font-size: 0.813rem;
  color: #64748b;
  margin-top: 0.25rem;
}

.place-order-btn {
  background: #2563eb;
  color: white;
  border: none;
  border-radius: 6px;
  padding: 0.5rem 1.25rem;
  font-size: 0.875rem;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s;
}

.place-order-btn:hover:not(:disabled) {
  background: #1d4ed8;
}

.place-order-btn:disabled {
  background: #cbd5e1;
  cursor: not-allowed;
}

.order-success {
  margin: 0 1.5rem 1rem;
  padding: 0.75rem 1rem;
  background: #d1fae5;
  border: 1px solid #10b981;
  border-radius: 6px;
  color: #065f46;
  font-size: 0.875rem;
}

.empty-state {
  padding: 2rem;
  text-align: center;
  color: #64748b;
}
</style>
