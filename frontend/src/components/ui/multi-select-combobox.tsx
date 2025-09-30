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
  

  // 处理选项分组逻辑（测试 / KCM* / AKCM* / 其他）
  const processedData = React.useMemo(() => {
    const q = searchValue.toLowerCase()
    const filtered = options.filter(option => option.label.toLowerCase().includes(q))

    const testGroups: GroupOption[] = []
    const kcmGroups: GroupOption[] = []
    const akcmGroups: GroupOption[] = []
    const otherGroups: GroupOption[] = []

    // 特殊的“客户名称含test”选项，归入“测试”分类（便于一次性选择/排除）
    const userNameTestOption: GroupOption = { value: "__USER_NAME_TEST__", label: "客户名称含test" }
    const shouldShowUserNameTest = userNameTestOption.label.toLowerCase().includes(q)
    if (shouldShowUserNameTest) {
      testGroups.push(userNameTestOption)
    }

    filtered.forEach(option => {
      const lower = option.label.toLowerCase()
      // 优先归入“测试”
      if (lower.includes('test') || option.label.includes('测试')) {
        testGroups.push(option)
        return
      }
      if (lower.startsWith('akcm')) {
        akcmGroups.push(option)
        return
      }
      if (lower.startsWith('kcm')) {
        kcmGroups.push(option)
        return
      }
      otherGroups.push(option)
    })

    const sortByLabel = (arr: GroupOption[]) => arr.sort((a, b) => a.label.localeCompare(b.label))

    const allGroups = sortByLabel([...testGroups.filter(g => g.value !== "__USER_NAME_TEST__"), ...kcmGroups, ...akcmGroups, ...otherGroups])

    return {
      testGroups: sortByLabel(testGroups).sort((a, b) => {
        if (a.value === "__USER_NAME_TEST__") return -1
        if (b.value === "__USER_NAME_TEST__") return 1
        return a.label.localeCompare(b.label)
      }),
      kcmGroups: sortByLabel(kcmGroups),
      akcmGroups: sortByLabel(akcmGroups),
      otherGroups: sortByLabel(otherGroups),
      allGroups,
      hasTestGroups: testGroups.length > 0,
      hasKcmGroups: kcmGroups.length > 0,
      hasAkcmGroups: akcmGroups.length > 0,
      hasOtherGroups: otherGroups.length > 0,
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

  // 分类全选/取消全选（支持：test / kcm / akcm / other）
  const toggleCategory = (categoryKey: 'test' | 'kcm' | 'akcm' | 'other') => {
    const categoryValues = (
      categoryKey === 'test' ? processedData.testGroups :
      categoryKey === 'kcm' ? processedData.kcmGroups :
      categoryKey === 'akcm' ? processedData.akcmGroups :
      processedData.otherGroups
    ).map(g => g.value)

    let newValue: string[]
    if (value.includes("__ALL__")) {
      // 从全选切换到“排除此分类”的具体选择模式
      const allRegularGroups = processedData.allGroups.map(g => g.value)
      newValue = allRegularGroups.filter(v => !categoryValues.includes(v))
      if (categoryKey === 'test' && categoryValues.includes("__USER_NAME_TEST__")) {
        newValue.push("__EXCLUDE_USER_NAME_TEST__")
      }
    } else {
      // 计算该分类当前实际选中数量（考虑排除标识）
      const actuallySelectedCount = categoryValues.filter(v => {
        if (categoryKey === 'test' && v === "__USER_NAME_TEST__" && value.includes("__EXCLUDE_USER_NAME_TEST__")) {
          return false
        }
        return value.includes(v)
      }).length

      const isAllSelected = actuallySelectedCount === categoryValues.length && !(categoryKey === 'test' && value.includes("__EXCLUDE_USER_NAME_TEST__"))

      if (isAllSelected) {
        // 取消选择该分类全部：添加测试语义的双重排除
        newValue = value.filter(v => !categoryValues.includes(v) && !(categoryKey === 'test' && v.startsWith("__EXCLUDE_")))
        if (categoryKey === 'test') {
          if (categoryValues.includes("__USER_NAME_TEST__")) {
            newValue.push("__EXCLUDE_USER_NAME_TEST__")
          }
          newValue.push("__EXCLUDE_GROUP_NAME_TEST__")
        }
      } else {
        // 选择该分类全部
        newValue = [...value.filter(v => !(categoryKey === 'test' && v.startsWith("__EXCLUDE_"))), ...categoryValues]
        newValue = [...new Set(newValue)]
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
  const getGenericSelectionState = (values: string[]) => {
    if (values.length === 0) return { checked: false, indeterminate: false }
    if (value.includes("__ALL__")) return { checked: true, indeterminate: false }
    const selectedCount = values.filter(v => value.includes(v)).length
    if (selectedCount === 0) return { checked: false, indeterminate: false }
    if (selectedCount === values.length) return { checked: true, indeterminate: false }
    return { checked: false, indeterminate: true }
  }
  const kcmSelectionState = getGenericSelectionState(processedData.kcmGroups.map(g => g.value))
  const akcmSelectionState = getGenericSelectionState(processedData.akcmGroups.map(g => g.value))
  const otherSelectionState = getGenericSelectionState(processedData.otherGroups.map(g => g.value))

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

              {/* 测试组别分组 */}
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
                            toggleCategory('test')
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
                        <span>测试 ({processedData.testGroups.length})</span>
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

              {/* KCM* 分组 */}
              {processedData.hasKcmGroups && (
                <CommandGroup>
                  <div className="px-2 py-1.5 text-xs font-semibold text-muted-foreground">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-auto p-0 font-semibold justify-start w-full"
                      onClick={(e) => {
                        e.preventDefault()
                        toggleGroupExpansion("kcm")
                      }}
                    >
                      <div className="flex items-center space-x-2 w-full">
                        {expandedGroups.has("kcm") ? (
                          <ChevronDown className="h-3 w-3" />
                        ) : (
                          <ChevronRight className="h-3 w-3" />
                        )}
                        <div
                          className="flex items-center"
                          onClick={(e) => {
                            e.stopPropagation()
                            toggleCategory('kcm')
                          }}
                        >
                          <Checkbox
                            checked={kcmSelectionState.checked}
                            onChange={() => {}}
                            className="pointer-events-none"
                          />
                          {kcmSelectionState.indeterminate && (
                            <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                              <div className="w-2 h-0.5 bg-current rounded" />
                            </div>
                          )}
                        </div>
                        <span>KCM* ({processedData.kcmGroups.length})</span>
                      </div>
                    </Button>
                  </div>
                  {expandedGroups.has("kcm") && processedData.kcmGroups.map((option) => (
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

              {/* AKCM* 分组 */}
              {processedData.hasAkcmGroups && (
                <CommandGroup>
                  <div className="px-2 py-1.5 text-xs font-semibold text-muted-foreground">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-auto p-0 font-semibold justify-start w-full"
                      onClick={(e) => {
                        e.preventDefault()
                        toggleGroupExpansion("akcm")
                      }}
                    >
                      <div className="flex items-center space-x-2 w-full">
                        {expandedGroups.has("akcm") ? (
                          <ChevronDown className="h-3 w-3" />
                        ) : (
                          <ChevronRight className="h-3 w-3" />
                        )}
                        <div
                          className="flex items-center"
                          onClick={(e) => {
                            e.stopPropagation()
                            toggleCategory('akcm')
                          }}
                        >
                          <Checkbox
                            checked={akcmSelectionState.checked}
                            onChange={() => {}}
                            className="pointer-events-none"
                          />
                          {akcmSelectionState.indeterminate && (
                            <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                              <div className="w-2 h-0.5 bg-current rounded" />
                            </div>
                          )}
                        </div>
                        <span>AKCM* ({processedData.akcmGroups.length})</span>
                      </div>
                    </Button>
                  </div>
                  {expandedGroups.has("akcm") && processedData.akcmGroups.map((option) => (
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

              {/* 其他组别 */}
              {processedData.hasOtherGroups && (
                <CommandGroup>
                  <div className="px-2 py-1.5 text-xs font-semibold text-muted-foreground">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-auto p-0 font-semibold justify-start w-full"
                      onClick={(e) => {
                        e.preventDefault()
                        toggleGroupExpansion("other")
                      }}
                    >
                      <div className="flex items-center space-x-2 w-full">
                        {expandedGroups.has("other") ? (
                          <ChevronDown className="h-3 w-3" />
                        ) : (
                          <ChevronRight className="h-3 w-3" />
                        )}
                        <div
                          className="flex items-center"
                          onClick={(e) => {
                            e.stopPropagation()
                            toggleCategory('other')
                          }}
                        >
                          <Checkbox
                            checked={otherSelectionState.checked}
                            onChange={() => {}}
                            className="pointer-events-none"
                          />
                          {otherSelectionState.indeterminate && (
                            <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                              <div className="w-2 h-0.5 bg-current rounded" />
                            </div>
                          )}
                        </div>
                        <span>其他 ({processedData.otherGroups.length})</span>
                      </div>
                    </Button>
                  </div>
                  {expandedGroups.has("other") && processedData.otherGroups.map((option) => (
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
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>

    </div>
  )
}
