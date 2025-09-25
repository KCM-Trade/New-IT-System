"use client"

import * as React from "react"
import { Check, ChevronsUpDown, ChevronDown, ChevronRight } from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"

interface GroupOption {
  value: string
  label: string
}

interface MultiSelectComboboxProps {
  options: GroupOption[]
  value: string[]
  onValueChange: (value: string[]) => void
  placeholder?: string
  searchPlaceholder?: string
  className?: string
}

export function MultiSelectCombobox({
  options,
  value,
  onValueChange,
  placeholder = "选择选项...",
  searchPlaceholder = "搜索组别...",
  className
}: MultiSelectComboboxProps) {
  const [open, setOpen] = React.useState(false)
  const [searchValue, setSearchValue] = React.useState("")
  const [expandedGroups, setExpandedGroups] = React.useState<Set<string>>(new Set())
  
  // 移动端检测
  const isMobile = React.useMemo(() => {
    if (typeof window === 'undefined') return false
    return window.innerWidth < 768
  }, [])
  

  // 处理选项分组逻辑
  const processedData = React.useMemo(() => {
    // 过滤搜索结果
    const filtered = options.filter(option => 
      option.label.toLowerCase().includes(searchValue.toLowerCase())
    )

    // 按组分类 - Test组别和所有组别
    const testGroups: GroupOption[] = []
    const allFilteredGroups: GroupOption[] = []

    // 添加特殊的"客户名称含test"选项到test组别
    const userNameTestOption: GroupOption = {
      value: "__USER_NAME_TEST__",
      label: "客户名称含test"
    }

    // 检查是否应该显示"客户名称含test"选项（基于搜索）
    const shouldShowUserNameTest = userNameTestOption.label.toLowerCase().includes(searchValue.toLowerCase())

    if (shouldShowUserNameTest) {
      testGroups.push(userNameTestOption)
    }

    filtered.forEach(option => {
      allFilteredGroups.push(option)
      if (option.label.toLowerCase().includes('test')) {
        testGroups.push(option)
      }
    })

    return {
      testGroups: testGroups.sort((a, b) => {
        // "客户名称含test"选项始终在最上方
        if (a.value === "__USER_NAME_TEST__") return -1
        if (b.value === "__USER_NAME_TEST__") return 1
        return a.label.localeCompare(b.label)
      }),
      allGroups: allFilteredGroups.sort((a, b) => a.label.localeCompare(b.label)),
      hasTestGroups: testGroups.length > 0,
      hasAllGroups: allFilteredGroups.length > 0
    }
  }, [options, searchValue])

  // 切换组展开状态
  const toggleGroupExpansion = (groupKey: string) => {
    const newExpanded = new Set(expandedGroups)
    if (newExpanded.has(groupKey)) {
      newExpanded.delete(groupKey)
    } else {
      newExpanded.add(groupKey)
    }
    setExpandedGroups(newExpanded)
  }

  // 切换单个选项选择状态
  const toggleOption = (optionValue: string) => {
    let newValue: string[]
    
    if (value.includes("__ALL__")) {
      // 当前是全选状态，切换到具体选择模式
      // 获取所有可能的选项值（包括常规组别和特殊选项）
      const allRegularValues = processedData.allGroups.map(g => g.value)
      const allPossibleValues = [...allRegularValues]
      
      // 如果特殊的"客户名称含test"选项存在，也加入到所有可能的值中
      if (processedData.testGroups.some(g => g.value === "__USER_NAME_TEST__")) {
        allPossibleValues.push("__USER_NAME_TEST__")
      }
      
      // 移除要取消选择的选项
      newValue = allPossibleValues.filter(v => v !== optionValue)
      
      // 特殊处理：如果取消选择的是"客户名称含test"，需要明确排除这类记录
      if (optionValue === "__USER_NAME_TEST__") {
        newValue.push("__EXCLUDE_USER_NAME_TEST__")
      }
    } else {
      // 正常的切换逻辑
      if (value.includes(optionValue)) {
        newValue = value.filter(v => v !== optionValue)
        // 如果取消选择"客户名称含test"，并且当前不是在排除模式，则添加排除标识符
        if (optionValue === "__USER_NAME_TEST__" && !value.includes("__EXCLUDE_USER_NAME_TEST__")) {
          // 检查是否已经选择了所有常规组别，如果是，则需要排除客户名称含test
          const allRegularValues = processedData.allGroups.map(g => g.value)
          const selectedRegularValues = value.filter(v => v !== "__USER_NAME_TEST__" && v !== "__EXCLUDE_USER_NAME_TEST__")
          
          // 如果选择了大部分常规组别，则添加排除标识符
          if (selectedRegularValues.length >= allRegularValues.length * 0.8) {
            newValue.push("__EXCLUDE_USER_NAME_TEST__")
          }
        }
      } else {
        // 添加选项，同时移除可能存在的排除标识符
        newValue = [...value.filter(v => v !== "__EXCLUDE_USER_NAME_TEST__"), optionValue]
      }
    }
    
    onValueChange(newValue)
  }

  // 全选/取消全选所有选项
  const toggleAllOptions = () => {
    if (value.includes("__ALL__")) {
      // 取消全选，清空所有选择
      onValueChange([])
    } else {
      // 全选，只设置"__ALL__"标识符
      onValueChange(["__ALL__"])
    }
  }

  // Test组别全选/取消全选
  const toggleTestGroups = () => {
    const testValues = processedData.testGroups.map(g => g.value)
    let newValue: string[]
    
    if (value.includes("__ALL__")) {
      // 当前是全选状态，切换到取消Test组别的具体选择模式
      const allRegularGroups = processedData.allGroups.map(g => g.value)
      const allGroupsIncludingSpecial = [...allRegularGroups]
      
      // 如果"客户名称含test"选项存在且在testGroups中，确保它也被考虑
      if (testValues.includes("__USER_NAME_TEST__") && !allGroupsIncludingSpecial.includes("__USER_NAME_TEST__")) {
        allGroupsIncludingSpecial.push("__USER_NAME_TEST__")
      }
      
      newValue = allGroupsIncludingSpecial.filter(v => !testValues.includes(v))
      
      // 对于test组别中的每个被取消的选项，如果是特殊选项，添加排除标识符
      if (testValues.includes("__USER_NAME_TEST__")) {
        newValue.push("__EXCLUDE_USER_NAME_TEST__")
      }
    } else {
      // 检查当前test组别的选择状态（考虑排除标识符）
      const actuallySelectedTestCount = testValues.filter(v => {
        if (v === "__USER_NAME_TEST__" && value.includes("__EXCLUDE_USER_NAME_TEST__")) {
          return false
        }
        return value.includes(v)
      }).length
      
      const allTestSelected = actuallySelectedTestCount === testValues.length && !value.includes("__EXCLUDE_USER_NAME_TEST__")
      
      if (allTestSelected) {
        // 取消选择所有Test组别（包括特殊选项）
        newValue = value.filter(v => !testValues.includes(v) && !v.startsWith("__EXCLUDE_"))
        // 添加排除标识符来排除客户名称含test的记录
        if (testValues.includes("__USER_NAME_TEST__")) {
          newValue.push("__EXCLUDE_USER_NAME_TEST__")
        }
      } else {
        // 选择所有Test组别（包括特殊选项）
        newValue = [...value.filter(v => !v.startsWith("__EXCLUDE_")), ...testValues]
        newValue = [...new Set(newValue)] // 去重
      }
    }
    
    onValueChange(newValue)
  }


  // 计算显示文本
  const getDisplayText = () => {
    // 过滤掉内部标识符，只计算用户可见的选择
    const visibleValues = value.filter(v => !v.startsWith("__EXCLUDE_"))
    
    if (visibleValues.length === 0) return placeholder
    
    if (visibleValues.includes("__ALL__")) {
      // 全选状态：包括所有常规组别 + 特殊的"客户名称含test"选项
      let totalCount = options.length + (processedData.testGroups.some(g => g.value === "__USER_NAME_TEST__") ? 1 : 0)
      
      // 如果有排除标识符，减去相应的数量
      if (value.includes("__EXCLUDE_USER_NAME_TEST__")) {
        totalCount -= 1
      }
      
      return `全部组别 (${totalCount})`
    }
    
    // 计算选中的常规组别数量
    const selectedRegularOptions = options.filter(opt => visibleValues.includes(opt.value))
    // 计算选中的特殊选项数量
    const selectedSpecialCount = visibleValues.includes("__USER_NAME_TEST__") ? 1 : 0
    const totalSelected = selectedRegularOptions.length + selectedSpecialCount
    
    if (totalSelected === 0) return placeholder
    
    return `已选择 ${totalSelected} 个组别`
  }

  // 检查选项是否被选中（考虑"__ALL__"状态和排除标识符）
  const isOptionSelected = (optionValue: string) => {
    // 如果有对应的排除标识符，则该选项不应显示为选中
    if (optionValue === "__USER_NAME_TEST__" && value.includes("__EXCLUDE_USER_NAME_TEST__")) {
      return false
    }
    
    return value.includes("__ALL__") || value.includes(optionValue)
  }

  // 检查Test组别的选择状态
  const getTestGroupsSelectionState = () => {
    const testValues = processedData.testGroups.map(g => g.value)
    if (testValues.length === 0) return { checked: false, indeterminate: false }
    
    if (value.includes("__ALL__")) {
      // 全选状态下，如果有排除标识符，需要特殊处理
      if (value.includes("__EXCLUDE_USER_NAME_TEST__")) {
        // 检查除了被排除的选项外，其他test选项是否都被选中
        const nonExcludedTestValues = testValues.filter(v => v !== "__USER_NAME_TEST__")
        if (nonExcludedTestValues.length === 0) return { checked: false, indeterminate: false }
        return { checked: false, indeterminate: true }
      }
      return { checked: true, indeterminate: false }
    }
    
    // 计算实际选中的test选项数量（排除被排除的选项）
    const selectedTestCount = testValues.filter(v => {
      if (v === "__USER_NAME_TEST__" && value.includes("__EXCLUDE_USER_NAME_TEST__")) {
        return false
      }
      return value.includes(v)
    }).length
    
    if (selectedTestCount === 0) return { checked: false, indeterminate: false }
    if (selectedTestCount === testValues.length && !value.includes("__EXCLUDE_USER_NAME_TEST__")) {
      return { checked: true, indeterminate: false }
    }
    return { checked: false, indeterminate: true }
  }

  const testSelectionState = getTestGroupsSelectionState()

  return (
    <div className={cn("flex flex-col gap-2", className)}>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="outline"
            role="combobox"
            aria-expanded={open}
            className="h-9 w-52 justify-between text-left touch-manipulation"
          >
            <span className="truncate">{getDisplayText()}</span>
            <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
          </Button>
        </PopoverTrigger>
        <PopoverContent 
          className="w-[calc(100vw-2rem)] md:w-80 p-0 max-h-[30vh] md:max-h-[50vh] overflow-hidden" 
          align={isMobile ? "center" : "start"}
          side="bottom"
          sideOffset={4}
          avoidCollisions={true}
          collisionPadding={isMobile ? 24 : 16}
          onOpenAutoFocus={(e: Event) => {
            // 移动端防止自动聚焦导致键盘弹起
            if (isMobile) {
              e.preventDefault()
            }
          }}
        >
          <Command>
            <CommandInput 
              placeholder={searchPlaceholder} 
              value={searchValue}
              onValueChange={setSearchValue}
              className="h-9" 
              autoComplete="off"
              autoCorrect="off"
              autoCapitalize="none"
              spellCheck={false}
            />
            <CommandList className="max-h-48 md:max-h-80 overflow-y-auto">
              <CommandEmpty>未找到组别。</CommandEmpty>
              
              {/* 全部组别选项 */}
              <CommandGroup>
                <CommandItem
                  onSelect={toggleAllOptions}
                  className="flex items-center space-x-2 min-h-10 touch-manipulation"
                >
                  <Checkbox
                    checked={value.includes("__ALL__")}
                    onChange={() => {}}
                    className="pointer-events-none"
                  />
                  <span className="font-medium">全部组别</span>
                  <Check
                    className={cn(
                      "ml-auto h-4 w-4",
                      value.includes("__ALL__") ? "opacity-100" : "opacity-0"
                    )}
                  />
                </CommandItem>
              </CommandGroup>

              {/* Test组别分组 */}
              {processedData.hasTestGroups && (
                <CommandGroup>
                  <div className="px-2 py-1.5 text-xs font-semibold text-muted-foreground">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-auto p-0 font-semibold justify-start w-full"
                      onClick={(e) => {
                        e.preventDefault()
                        toggleGroupExpansion("test")
                      }}
                    >
                      <div className="flex items-center space-x-2 w-full">
                        {expandedGroups.has("test") ? (
                          <ChevronDown className="h-3 w-3" />
                        ) : (
                          <ChevronRight className="h-3 w-3" />
                        )}
                        <div
                          className="flex items-center"
                          onClick={(e) => {
                            e.stopPropagation()
                            toggleTestGroups()
                          }}
                        >
                          <Checkbox
                            checked={testSelectionState.checked}
                            onChange={() => {}}
                            className="pointer-events-none"
                          />
                          {testSelectionState.indeterminate && (
                            <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                              <div className="w-2 h-0.5 bg-current rounded" />
                            </div>
                          )}
                        </div>
                        <span>Test 组别 ({processedData.testGroups.length})</span>
                      </div>
                    </Button>
                  </div>

                  {expandedGroups.has("test") && processedData.testGroups.map((option) => (
                    <CommandItem
                      key={option.value}
                      onSelect={() => toggleOption(option.value)}
                      className="flex items-center space-x-2 pl-8 min-h-10 touch-manipulation"
                    >
                      <Checkbox
                        checked={isOptionSelected(option.value)}
                        onChange={() => {}}
                        className="pointer-events-none"
                      />
                      <span>{option.label}</span>
                      <Check
                        className={cn(
                          "ml-auto h-4 w-4",
                          isOptionSelected(option.value) ? "opacity-100" : "opacity-0"
                        )}
                      />
                    </CommandItem>
                  ))}
                </CommandGroup>
              )}

              {/* 所有组别（除Test组别外的直接显示） */}
              {processedData.hasAllGroups && (
                <CommandGroup>
                  <div className="px-2 py-1.5 text-xs font-semibold text-muted-foreground">
                    所有组别
                  </div>

                  {/* 显示非Test组别 */}
                  {processedData.allGroups
                    .filter(option => !option.label.toLowerCase().includes('test'))
                    .map((option) => (
                    <CommandItem
                      key={option.value}
                      onSelect={() => toggleOption(option.value)}
                      className="flex items-center space-x-2 pl-4 min-h-10 touch-manipulation"
                    >
                      <Checkbox
                        checked={isOptionSelected(option.value)}
                        onChange={() => {}}
                        className="pointer-events-none"
                      />
                      <span>{option.label}</span>
                      <Check
                        className={cn(
                          "ml-auto h-4 w-4",
                          isOptionSelected(option.value) ? "opacity-100" : "opacity-0"
                        )}
                      />
                    </CommandItem>
                  ))}
                </CommandGroup>
              )}
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>

    </div>
  )
}
